import usb.core
import usb.util
import struct
import time
import io
import sys
import os
import socket
import subprocess
import logging
from datetime import datetime
from collections import deque
from PIL import Image, ImageDraw, ImageFont
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad
import psutil

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(levelname)s %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("lcd_driver")


# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
VID         = 0x1cbe
PID         = 0xa065
DES_KEY     = b'slv3tuzx'
W, H        = 720, 1472
APP_START   = time.time()
HISTORY_LEN = 180  # 180 samples × 1s = 3 min
TITLE       = os.environ.get("LCD_TITLE", socket.gethostname())


def _detect_gpu_name() -> str:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            timeout=2,
        ).decode().strip().splitlines()
        return out[0] if out else "GPU"
    except Exception:
        return "GPU"


GPU_NAME = os.environ.get("LCD_GPU_LABEL", _detect_gpu_name())


# ─────────────────────────────────────────────
#  ROLLING HISTORY
# ─────────────────────────────────────────────
history = {
    "cpu_pct":  deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN),
    "cpu_temp": deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN),
    "gpu_pct":  deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN),
    "gpu_temp": deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN),
    "gpu_mem":  deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN),
    "ram_pct":  deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN),
}


# ─────────────────────────────────────────────
#  TIMESTAMP
# ─────────────────────────────────────────────
def get_timestamp() -> int:
    return int((time.time() - APP_START) * 1000) & 0xFFFFFFFF


# ─────────────────────────────────────────────
#  HEADER BUILDER
# ─────────────────────────────────────────────
def build_base_cmd(cmd_byte: int) -> bytearray:
    header = bytearray(500)
    header[0] = cmd_byte
    header[2] = 0x1A
    header[3] = 0x6D
    struct.pack_into('<I', header, 4, get_timestamp())
    return header


# ─────────────────────────────────────────────
#  ENCRYPTION  (DES-CBC, key = IV = slv3tuzx)
# ─────────────────────────────────────────────
def encrypt_header(header_500: bytearray) -> bytes:
    padded    = pad(bytes(header_500), DES.block_size, style='pkcs7')
    cipher    = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_KEY)
    encrypted = cipher.encrypt(padded)

    result        = bytearray(512)
    result[0:504] = encrypted[:504]
    result[510]   = 0xA1
    result[511]   = 0x1A
    return bytes(result)


# ─────────────────────────────────────────────
#  COMMAND PACKETS
# ─────────────────────────────────────────────
def build_rotate_cmd(rotation: int = 0) -> bytes:
    header = build_base_cmd(0x0D)
    header[8] = rotation
    return encrypt_header(header)


def build_clock_packet(is_stop: bool) -> bytes:
    cmd    = 0x34 if is_stop else 0x33
    header = build_base_cmd(cmd)
    if not is_stop:
        now        = datetime.now()
        header[8]  = (now.year >> 8) & 0xFF
        header[9]  = now.year & 0xFF
        header[10] = now.month
        header[11] = now.day
        header[12] = now.hour
        header[13] = now.minute
        header[14] = now.second
        header[15] = 2
    return encrypt_header(header)


# ─────────────────────────────────────────────
#  STATS COLLECTION
# ─────────────────────────────────────────────
def get_gpu_stats() -> dict:
    try:
        out = subprocess.check_output([
            "nvidia-smi",
            "--query-gpu=utilization.gpu,temperature.gpu,power.draw,memory.used,memory.total",
            "--format=csv,noheader,nounits"
        ], timeout=2).decode().strip()
        gpu_util, gpu_temp, gpu_power, mem_used, mem_total = [x.strip() for x in out.split(",")]
        return {
            "util": int(gpu_util),
            "temp": int(float(gpu_temp)),
            "power": float(gpu_power),
            "mem_used": float(mem_used),
            "mem_total": float(mem_total),
        }
    except Exception as e:
        log.debug(f"nvidia-smi failed: {e}")
        return {"util": 0, "temp": 0, "power": 0.0, "mem_used": 0.0, "mem_total": 1.0}


def get_cpu_stats() -> dict:
    result = {"temp": None, "power": None}
    try:
        temps = psutil.sensors_temperatures()
        for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
            if key in temps and temps[key]:
                result["temp"] = temps[key][0].current
                break
    except Exception as e:
        log.debug(f"CPU temp read failed: {e}")
    try:
        p1 = int(subprocess.check_output(
            ["cat", "/sys/class/powercap/intel-rapl:0/energy_uj"], timeout=1).decode().strip())
        time.sleep(0.1)
        p2 = int(subprocess.check_output(
            ["cat", "/sys/class/powercap/intel-rapl:0/energy_uj"], timeout=1).decode().strip())
        result["power"] = (p2 - p1) / 0.1 / 1_000_000
    except Exception:
        try:
            p1 = int(subprocess.check_output(
                ["cat", "/sys/class/powercap/amd-energy:0/energy_uj"], timeout=1).decode().strip())
            time.sleep(0.1)
            p2 = int(subprocess.check_output(
                ["cat", "/sys/class/powercap/amd-energy:0/energy_uj"], timeout=1).decode().strip())
            result["power"] = (p2 - p1) / 0.1 / 1_000_000
        except Exception as e:
            log.debug(f"CPU power read failed: {e}")
    return result


# ─────────────────────────────────────────────
#  IMAGE GENERATION
# ─────────────────────────────────────────────
def create_blank_png() -> bytes:
    img = Image.new('RGBA', (W, H), color=(0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def create_blank_jpeg() -> bytes:
    img = Image.new('RGB', (H, W), color=(0, 0, 0))
    img = img.rotate(-90, expand=True)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=95)
    return buf.getvalue()


def create_deyloop_image(background_image) -> bytes:
    buf = io.BytesIO()
    with Image.open(background_image) as img:
        img = img.resize(size=(1472, 720))
        img = img.rotate(-90, expand=True)
        img.save(buf, format='JPEG', quality=95)
    jpg = buf.getvalue()
    assert len(jpg) <= 512000, f"Image too large: {len(jpg)} bytes"
    log.info(f"Background image loaded: {len(jpg)} bytes")
    return jpg


def create_stats_png() -> bytes:
    canvas_w, canvas_h = 1472, 720
    img  = Image.new('RGBA', (canvas_w, canvas_h), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Fonts ──────────────────────────────────────────────────────────────
    try:
        font_large  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 48)
        font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 32)
        font_small  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 24)
        font_tiny   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 18)
    except IOError:
        log.warning("DejaVu fonts not found, falling back to default font")
        font_large = font_medium = font_small = font_tiny = ImageFont.load_default()

    # ── Pull stats ──────────────────────────────────────────────────────────
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_freq    = psutil.cpu_freq()
    cpu         = get_cpu_stats()
    ram         = psutil.virtual_memory()
    gpu         = get_gpu_stats()

    # ── Update history ──────────────────────────────────────────────────────
    history["cpu_pct"].append(cpu_percent)
    history["cpu_temp"].append(cpu["temp"] or 0.0)
    history["gpu_pct"].append(float(gpu["util"]))
    history["gpu_temp"].append(float(gpu["temp"]))
    if gpu["mem_total"] > 0:
        history["gpu_mem"].append(gpu["mem_used"] / gpu["mem_total"] * 100)
    history["ram_pct"].append(ram.percent)

    log.debug(f"Stats — CPU: {cpu_percent:.0f}% {cpu['temp']}°C  "
              f"GPU: {gpu['util']}% {gpu['temp']}°C {gpu['power']:.0f}W  "
              f"RAM: {ram.percent:.0f}%")

    # ── Helpers ────────────────────────────────────────────────────────────
    def draw_card(x, y, w, h, alpha=150):
        draw.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=(10, 10, 10, alpha))

    def draw_bar(x, y, w, label, value_pct, color, text):
        BAR_H     = 36
        LABEL_GAP = 28
        draw.text((x, y), label, font=font_small, fill=(180, 180, 180, 220))
        y += LABEL_GAP
        draw.rounded_rectangle([x, y, x + w, y + BAR_H], radius=8, fill=(40, 40, 40, 200))
        fill_w = int(w * min(max(value_pct, 0), 100) / 100)
        if fill_w > 0:
            draw.rounded_rectangle([x, y, x + fill_w, y + BAR_H], radius=8, fill=(*color, 230))
        bbox   = draw.textbbox((0, 0), text, font=font_small)
        text_w = bbox[2] - bbox[0]
        draw.text((x + w - text_w - 10, y + (BAR_H - 24) // 2 + 2),
                  text, font=font_small, fill=(255, 255, 255, 240))
        return y + BAR_H + 14

    def draw_graph(x, y, w, h, series, y_min, y_max, title):
        AXIS_W  = 36
        PAD_TOP = 22
        PAD_BOT = 4
        gx = x + AXIS_W
        gy = y + PAD_TOP
        gw = w - AXIS_W - 4
        gh = h - PAD_TOP - PAD_BOT

        draw_card(x, y, w, h, alpha=160)
        draw.text((gx, y + 3), title, font=font_tiny, fill=(200, 200, 200, 220))

        y_range = y_max - y_min if y_max != y_min else 1
        for pct in (0, 25, 50, 75, 100):
            val = y_min + (pct / 100) * y_range
            py  = gy + gh - int((val - y_min) / y_range * gh)
            draw.line([(gx, py), (gx + gw, py)], fill=(60, 60, 60, 120), width=1)
            label = f"{int(val)}"
            lw    = draw.textbbox((0, 0), label, font=font_tiny)[2]
            draw.text((gx - lw - 4, py - 9), label, font=font_tiny, fill=(120, 120, 120, 200))

        for data, color, _ in series:
            pts    = list(data)
            n      = len(pts)
            coords = []
            for i, v in enumerate(pts):
                px = gx + int(i / (n - 1) * gw) if n > 1 else gx
                py = gy + gh - int((v - y_min) / y_range * gh)
                py = max(gy, min(gy + gh, py))
                coords.append((px, py))
            if len(coords) >= 2:
                draw.line(coords, fill=(*color, 230), width=2)
            if coords:
                cx, cy = coords[-1]
                draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(*color, 255))

        # Legend — top right
        lx = x + w - 4
        for _, color, label in reversed(series):
            lw  = draw.textbbox((0, 0), label, font=font_tiny)[2]
            lx -= lw
            draw.text((lx, y + 3), label, font=font_tiny, fill=(200, 200, 200, 220))
            lx -= 16
            draw.rectangle([lx, y + 5, lx + 12, y + 15], fill=(*color, 220))
            lx -= 12

    # ── Layout constants ───────────────────────────────────────────────────
    PAD    = 28
    GUTTER = 14

    col1_x = PAD
    col1_w = 690
    col2_x = col1_x + col1_w + GUTTER
    col2_w = canvas_w - col2_x - PAD

    TITLE_H    = 70
    TITLE_Y    = PAD
    CPU_CARD_H = 176
    GPU_CARD_H = 176
    BARS_Y     = TITLE_Y + TITLE_H + GUTTER
    RAM_NET_H  = 86
    BOTTOM_Y   = canvas_h - PAD - RAM_NET_H
    GRAPH_Y    = BARS_Y + CPU_CARD_H + GUTTER
    GRAPH_H    = BOTTOM_Y - GRAPH_Y - GUTTER

    # ── Title strip ────────────────────────────────────────────────────────
    draw_card(PAD, TITLE_Y, canvas_w - 2*PAD, TITLE_H, alpha=185)
    draw.text((PAD + 18, TITLE_Y + 10), TITLE, font=font_large, fill=(255, 255, 255, 245))
    ts   = datetime.now().strftime("%H:%M:%S   %d %b %Y")
    ts_w = draw.textbbox((0, 0), ts, font=font_small)[2]
    draw.text((canvas_w - PAD - ts_w - 18, TITLE_Y + 22), ts,
              font=font_small, fill=(150, 150, 150, 215))

    # ── Left: CPU bars ─────────────────────────────────────────────────────
    draw_card(col1_x, BARS_Y, col1_w, CPU_CARD_H, alpha=150)
    y = BARS_Y + 12

    cpu_text = f"{cpu_percent:.0f}%"
    if cpu_freq:
        cpu_text += f"  {cpu_freq.current / 1000:.2f} GHz"
    if cpu["power"] is not None:
        cpu_text += f"  {cpu['power']:.0f} W"
    y = draw_bar(col1_x + 16, y, col1_w - 32, "CPU USAGE", cpu_percent, (0, 180, 255), cpu_text)

    if cpu["temp"] is not None:
        tc = (255, 70, 70) if cpu["temp"] > 80 else (255, 200, 0) if cpu["temp"] > 65 else (0, 210, 110)
        draw_bar(col1_x + 16, y, col1_w - 32, "CPU TEMP", cpu["temp"], tc, f"{cpu['temp']:.0f} °C")

    # ── Right: GPU bars ────────────────────────────────────────────────────
    draw_card(col2_x, BARS_Y, col2_w, GPU_CARD_H, alpha=150)
    y = BARS_Y + 12

    y = draw_bar(col2_x + 16, y, col2_w - 32, f"GPU USAGE  ({GPU_NAME})",
                 gpu["util"], (255, 140, 0), f"{gpu['util']}%  {gpu['power']:.0f} W")
    gtc = (255, 70, 70) if gpu["temp"] > 85 else (255, 200, 0) if gpu["temp"] > 70 else (0, 210, 110)
    draw_bar(col2_x + 16, y, col2_w - 32, "GPU TEMP",
             gpu["temp"], gtc, f"{gpu['temp']} °C")

    # ── Graphs ─────────────────────────────────────────────────────────────
    draw_graph(
        x=col1_x, y=GRAPH_Y, w=col1_w, h=GRAPH_H,
        series=[
            (history["cpu_pct"],  (0, 180, 255), "CPU %"),
            (history["cpu_temp"], (0, 210, 110),  "Temp °C"),
            (history["ram_pct"],  (160, 80, 255), "RAM %"),
        ],
        y_min=0, y_max=100,
        title="CPU  —  3 min history"
    )
    draw_graph(
        x=col2_x, y=GRAPH_Y, w=col2_w, h=GRAPH_H,
        series=[
            (history["gpu_pct"],  (255, 140, 0),  "GPU %"),
            (history["gpu_temp"], (0, 210, 110),   "Temp °C"),
            (history["gpu_mem"],  (160, 80, 255),  "VRAM %"),
        ],
        y_min=0, y_max=100,
        title="GPU  —  3 min history"
    )

    # ── Bottom strip: RAM | GPU Memory ─────────────────────────────────────
    draw_card(col1_x, BOTTOM_Y, col1_w, RAM_NET_H, alpha=150)
    draw_bar(col1_x + 16, BOTTOM_Y + 12, col1_w - 32, "RAM",
              ram.percent, (160, 80, 255),
              f"{ram.used / 1e9:.1f} / {ram.total / 1e9:.1f} GB  ({ram.percent:.0f}%)")

    draw_card(col2_x, BOTTOM_Y, col2_w, RAM_NET_H, alpha=150)
    gpu_mem_pct = gpu["mem_used"] / gpu["mem_total"] * 100 if gpu["mem_total"] > 0 else 0.0
    mem_color   = (255, 70, 70) if gpu_mem_pct > 90 else (255, 200, 0) if gpu_mem_pct > 75 else (160, 80, 255)
    draw_bar(col2_x + 16, BOTTOM_Y + 12, col2_w - 32, "GPU MEMORY",
              gpu_mem_pct, mem_color,
              f"{gpu['mem_used']:.0f} / {gpu['mem_total']:.0f} MB  ({gpu_mem_pct:.0f}%)")

    # ── Rotate to portrait ─────────────────────────────────────────────────
    img = img.rotate(-90, expand=True)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    png = buf.getvalue()
    assert len(png) <= 512000, f"PNG overlay too large: {len(png)} bytes"
    log.debug(f"Stats PNG rendered: {len(png)} bytes")
    return png


# ─────────────────────────────────────────────
#  PACKET BUILDERS
# ─────────────────────────────────────────────
def build_jpeg_packet(jpg_bytes: bytes) -> bytes:
    header = build_base_cmd(0x65)
    struct.pack_into('>I', header, 8, len(jpg_bytes))
    return encrypt_header(header) + jpg_bytes


def build_png_packet(png_bytes: bytes) -> bytes:
    header = build_base_cmd(0x66)
    struct.pack_into('>I', header, 8, len(png_bytes))
    return encrypt_header(header) + png_bytes


# ─────────────────────────────────────────────
#  STATS LOOP
# ─────────────────────────────────────────────
def drain_in(ep_in):
    try:
        while True:
            ep_in.read(512, timeout=50)
    except usb.core.USBError:
        pass


def run_stats_loop(push_chunked, ep_in, ep_out, interval: float = 1.0):
    log.info("Entering live stats loop")
    try:
        while True:
            tick_start = time.time()
            drain_in(ep_in)
            png_bytes = create_stats_png()
            push_chunked(build_png_packet(png_bytes), "StatsOverlay")
            elapsed = time.time() - tick_start
            time.sleep(max(0, interval - elapsed))
    except KeyboardInterrupt:
        log.info("Interrupted — clearing PNG overlay")
        try:
            ep_out.clear_halt()
        except Exception:
            pass
        push_chunked(build_png_packet(create_blank_png()), "ClearPngLayer")


# ─────────────────────────────────────────────
#  USB COMMUNICATION
# ─────────────────────────────────────────────
def push_to_lcd(background_image):
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        log.error("Device not found. Is the Lancool 207 Digital connected?")
        sys.exit(1)

    log.info(f"Found device: {VID:#06x}:{PID:#06x}")

    for cfg in dev:
        for intf in cfg:
            if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                try:
                    dev.detach_kernel_driver(intf.bInterfaceNumber)
                    log.info(f"Detached kernel driver from interface {intf.bInterfaceNumber}")
                except usb.core.USBError as e:
                    log.warning(f"Could not detach kernel driver: {e}")

    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        log.warning(f"set_configuration failed ({e}) — attempting soft cleanup")
        for cfg in dev:
            for intf in cfg:
                try:
                    usb.util.release_interface(dev, intf.bInterfaceNumber)
                except Exception:
                    pass
        usb.util.dispose_resources(dev)
        time.sleep(0.5)
        dev.set_configuration()

    usb.util.claim_interface(dev, 0)

    cfg  = dev.get_active_configuration()
    intf = cfg[(0, 0)]

    ep_out = usb.util.find_descriptor(intf, custom_match=lambda e:
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT and
        usb.util.endpoint_type(e.bmAttributes)          == usb.util.ENDPOINT_TYPE_BULK)

    ep_in  = usb.util.find_descriptor(intf, custom_match=lambda e:
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN and
        usb.util.endpoint_type(e.bmAttributes)          == usb.util.ENDPOINT_TYPE_BULK)

    if not ep_out or not ep_in:
        log.error("Could not find BULK IN/OUT endpoints")
        sys.exit(1)

    log.info(f"Endpoints — OUT: {ep_out.bEndpointAddress:#04x}  IN: {ep_in.bEndpointAddress:#04x}")

    def send_and_read(payload: bytes, name: str):
        ep_out.write(payload, timeout=200)
        log.debug(f"Sent {name} ({len(payload)} bytes)")
        try:
            ack = ep_in.read(512, timeout=200)
            log.debug(f"ACK {name}: {[hex(x) for x in ack[:4]]}")
        except usb.core.USBError:
            log.debug(f"No ACK for {name} (expected)")

    def push_chunked(full_payload: bytes, label: str):
        total  = len(full_payload)
        chunks = (total + 4095) // 4096
        log.debug(f"Pushing {label} — {total} bytes in {chunks} chunks")
        for i in range(0, total, 4096):
            chunk = full_payload[i:i + 4096]
            for attempt in range(3):
                try:
                    ep_out.write(chunk, timeout=5000)
                    break
                except usb.core.USBTimeoutError:
                    log.warning(f"Timeout on chunk {i//4096}, attempt {attempt+1}/3 — clearing halt")
                    try:
                        ep_out.clear_halt()
                        time.sleep(0.3)
                    except usb.core.USBError as he:
                        log.warning(f"clear_halt failed: {he}")
                        time.sleep(0.5)
            else:
                log.error(f"Chunk {i//4096} of {label} failed after 3 retries — skipping frame")
                try:
                    ep_out.clear_halt()
                except Exception:
                    pass
                return
        time.sleep(0.3)
        send_and_read(encrypt_header(build_base_cmd(0x79)), "StartPlay (0x79)")

    # ── Initialization sequence ───────────────────────────────────────────
    log.info("Initializing display")
    send_and_read(build_rotate_cmd(rotation=0),      "Rotate (0x0D)")
    time.sleep(0.1)
    send_and_read(build_clock_packet(is_stop=False), "SyncClock (0x33)")
    time.sleep(0.1)
    send_and_read(build_clock_packet(is_stop=True),  "StopClock (0x34)")
    time.sleep(0.2)

    log.info("Clearing display layers")
    push_chunked(build_png_packet(create_blank_png()),   "ClearPngLayer")
    time.sleep(0.1)
    push_chunked(build_jpeg_packet(create_blank_jpeg()), "ClearJpgLayer")
    time.sleep(0.1)

    log.info("Pushing background image")
    push_chunked(build_jpeg_packet(create_deyloop_image(background_image)), "JPG Background")
    time.sleep(0.2)

    run_stats_loop(push_chunked, ep_in, ep_out, interval=1.0)

    log.info("Releasing USB device")
    usb.util.release_interface(dev, 0)
    usb.util.dispose_resources(dev)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        log.error("Usage: lcd-driver <background_image>")
        sys.exit(1)
    push_to_lcd(sys.argv[1])

if __name__ == "__main__":
    main()
