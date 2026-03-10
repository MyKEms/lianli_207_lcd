import usb.core
import usb.util
import struct
import time
import io
import sys
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
VID       = 0x1cbe
PID       = 0xa065
DES_KEY   = b'slv3tuzx'
W, H      = 720, 1472
APP_START = time.time()


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
    header[8] = rotation  # 0 = native portrait
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
        header[15] = 2  # onlySync = True
    return encrypt_header(header)


# ─────────────────────────────────────────────
#  IMAGE GENERATION
# ─────────────────────────────────────────────

def create_blank_png() -> bytes:
    """ClearPngLayer — fully transparent PNG at native resolution."""
    img = Image.new('RGBA', (W, H), color=(0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def create_blank_jpeg() -> bytes:
    """ClearJpgLayer — blank black JPEG, pre-rotated."""
    img = Image.new('RGB', (H, W), color=(0, 0, 0))  # 1472x720
    img = img.rotate(-90, expand=True)                 # back to 720x1472
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=95)
    return buf.getvalue()


def create_deyloop_image(background_image) -> bytes:
    """Loads image at 1472x720, then rotates to 720x1472."""

    buf = io.BytesIO()
    with Image.open(background_image) as img:
        img = img.resize(size=(1472,720))
        img = img.rotate(-90, expand=True)
        img.save(buf, format='JPEG', quality=95)

    jpg = buf.getvalue()
    assert len(jpg) <= 512000, f"Image too large: {len(jpg)} bytes"
    print(f"[*] Generated image: {W}x{H}, {len(jpg)} bytes")
    return jpg


# ─────────────────────────────────────────────
#  PACKET BUILDERS
# ─────────────────────────────────────────────
def build_jpeg_packet(jpg_bytes: bytes) -> bytes:
    header = build_base_cmd(0x65)  # PushJpg — background layer
    struct.pack_into('>I', header, 8, len(jpg_bytes))
    return encrypt_header(header) + jpg_bytes


def build_png_packet(png_bytes: bytes) -> bytes:
    header = build_base_cmd(0x66)  # PushPng — overlay layer
    struct.pack_into('>I', header, 8, len(png_bytes))
    return encrypt_header(header) + png_bytes


# ─────────────────────────────────────────────
#  USB COMMUNICATION
# ─────────────────────────────────────────────
def push_to_lcd(background_image):
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("[-] Device not found. Is the Lancool 207 Digital connected?")
        sys.exit(1)

    print(f"[+] Found device: {VID:#06x}:{PID:#06x}")

    for cfg in dev:
        for intf in cfg:
            if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                try:
                    dev.detach_kernel_driver(intf.bInterfaceNumber)
                    print(f"[*] Detached kernel driver from interface {intf.bInterfaceNumber}")
                except usb.core.USBError as e:
                    print(f"[*] Could not detach: {e}")

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
        print("[-] Could not find BULK IN/OUT endpoints.")
        sys.exit(1)

    print(f"[+] Endpoints  OUT: {ep_out.bEndpointAddress:#04x}  IN: {ep_in.bEndpointAddress:#04x}")

    # ── Send command and read ACK ────────────────────────────────────────
    def send_and_read(payload: bytes, name: str):
        print(f"[*] Sending {name} ({len(payload)} bytes)...")
        ep_out.write(payload, timeout=200)
        try:
            ack = ep_in.read(512, timeout=200)
            print(f"    [+] ACK: {[hex(x) for x in ack[:4]]}")
        except usb.core.USBError as e:
            print(f"    [-] Read timeout (may be expected): {e}")

    # ── Push a full payload in 4KB chunks, then trigger render ───────────
    def push_chunked(full_payload: bytes, label: str):
        total  = len(full_payload)
        chunks = (total + 4095) // 4096
        print(f"[*] Pushing {label} — {total} bytes in {chunks} chunks...")
        for i in range(0, total, 4096):
            ep_out.write(full_payload[i:i + 4096], timeout=2000)
        time.sleep(0.1)
        send_and_read(encrypt_header(build_base_cmd(0x79)), "StartPlay (0x79)")

    # ── Full initialization sequence (mirrors ApplyTemplate exactly) ─────
    send_and_read(build_rotate_cmd(rotation=0),         "Rotate (0x0D, r=0)")
    time.sleep(0.1)

    send_and_read(build_clock_packet(is_stop=False),    "SyncClock (0x33)")
    time.sleep(0.1)

    send_and_read(build_clock_packet(is_stop=True),     "StopClock (0x34)")
    time.sleep(0.2)

    # ── Clear both layers (mirrors ClearPngLayer + ClearJpgLayer) ────────
    push_chunked(build_png_packet(create_blank_png()),   "ClearPngLayer (transparent 720x1472)")
    time.sleep(0.1)

    push_chunked(build_jpeg_packet(create_blank_jpeg()), "ClearJpgLayer (black 720x1472)")
    time.sleep(0.1)

    # ── Push the actual image ─────────────────────────────────────────────
    push_chunked(build_jpeg_packet(create_deyloop_image(background_image)), "Deyloop's Driver (720x1472)")

    print("\n[+] All done! Your screen should now show Deyloop's Driver.")

    usb.util.release_interface(dev, 0)
    usb.util.dispose_resources(dev)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
import sys
if __name__ == "__main__":
    background_image = sys.argv[1]
    push_to_lcd(background_image)

