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

# --- CONFIGURATION ---
VID = 0x1cbe
PID = 0xa065
DES_KEY = b'slv3tuzx'

APP_START = time.time()

def get_timestamp() -> int:
    ms_elapsed = int((time.time() - APP_START) * 1000)
    return ms_elapsed & 0xFFFFFFFF

def encrypt_header(header_500: bytearray) -> bytes:
    padded = pad(bytes(header_500), DES.block_size, style='pkcs7')
    cipher = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_KEY)
    encrypted_504 = cipher.encrypt(padded)
    
    final_header = bytearray(512)
    final_header[0:504] = encrypted_504
    final_header[510] = 0xA1
    final_header[511] = 0x1A
    return bytes(final_header)

def build_base_cmd(cmd_byte: int) -> bytearray:
    """Builds the standard 500-byte unencrypted header."""
    header = bytearray(500)
    header[0] = cmd_byte
    header[2] = 0x1A  # Magic
    header[3] = 0x6D  # Magic
    struct.pack_into('<I', header, 4, get_timestamp())
    return header

def build_clock_packet(is_stop: bool) -> bytes:
    """Builds the 512-byte SetClock (0x33) or StopClock (0x34) packet."""
    cmd = 0x34 if is_stop else 0x33
    header = build_base_cmd(cmd)
    
    if not is_stop:
        # SyncClock payload (8 bytes copied to offset 8)
        now = datetime.now()
        header[8]  = (now.year >> 8) & 0xFF
        header[9]  = now.year & 0xFF
        header[10] = now.month
        header[11] = now.day
        header[12] = now.hour
        header[13] = now.minute
        header[14] = now.second
        header[15] = 2  # onlySync = true
        
    return encrypt_header(header)
# 1. Correct dimensions
W, H = 720, 1472

def create_deyloop_image() -> bytes:
    img = Image.new('RGB', (W, H), color=(15, 15, 20))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
        sub_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    except:
        font = ImageFont.load_default()
        sub_font = font

    text = "Deyloop's Driver"
    sub_text = "PyUSB · Linux · Lancool 207"

    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((W - (bbox[2]-bbox[0])) / 2, H//2 - 80), text, font=font, fill=(0, 255, 128))

    bbox2 = draw.textbbox((0, 0), sub_text, font=sub_font)
    draw.text(((W - (bbox2[2]-bbox2[0])) / 2, H//2 + 20), sub_text, font=sub_font, fill=(150, 150, 150))

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=95)  # 95% quality to match ClearJpgLayer
    jpg = buf.getvalue()
    assert len(jpg) <= 512000
    print(f"[*] Generated image: {W}x{H}, {len(jpg)} bytes")
    return jpg

# 2. ClearJpgLayer equivalent — blank black JPEG at correct dimensions
def create_blank_image() -> bytes:
    img = Image.new('RGB', (W, H), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=95)
    return buf.getvalue()

def build_image_packet(jpg_bytes: bytes) -> bytes:
    header = build_base_cmd(0x65)
    struct.pack_into('>I', header, 8, len(jpg_bytes))
    return encrypt_header(header) + jpg_bytes

def push_to_lcd():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("[-] Device not found.")
        sys.exit(1)

    for cfg in dev:
        for intf in cfg:
            if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                try:
                    dev.detach_kernel_driver(intf.bInterfaceNumber)
                except:
                    pass

    dev.set_configuration()
    usb.util.claim_interface(dev, 0)

    cfg = dev.get_active_configuration()
    intf = cfg[(0,0)]

    ep_out = usb.util.find_descriptor(intf, custom_match=lambda e:
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT and
        usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)
    ep_in  = usb.util.find_descriptor(intf, custom_match=lambda e:
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN and
        usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)

    def send_and_read(payload, name):
        print(f"[*] Sending {name} ({len(payload)} bytes)...")
        ep_out.write(payload, timeout=200)
        try:
            ack = ep_in.read(512, timeout=200)
            print(f"    [+] ACK: {[hex(x) for x in ack[:4]]}")
        except usb.core.USBError as e:
            print(f"    [-] Read timeout (may be expected): {e}")

    def push_jpeg(jpg_bytes, label):
        """Push a JPEG in 4KB chunks and send StartPlay."""
        full_payload = build_image_packet(jpg_bytes)
        print(f"[*] Pushing {label} ({len(full_payload)} bytes in chunks)...")
        for i in range(0, len(full_payload), 4096):
            ep_out.write(full_payload[i:i+4096], timeout=2000)
        time.sleep(0.1)
        # Trigger render
        send_and_read(encrypt_header(build_base_cmd(0x79)), "StartPlay (0x79)")

    # 3. Correct sequence: Rotate → SyncClock → StopClock → ClearJpgLayer → PushJpg
    send_and_read(encrypt_header(build_base_cmd(0x0D)), "Rotate (0x0D)")
    time.sleep(0.1)
    send_and_read(build_clock_packet(is_stop=False), "SyncClock (0x33)")
    time.sleep(0.1)
    send_and_read(build_clock_packet(is_stop=True),  "StopClock (0x34)")
    time.sleep(0.2)

    push_jpeg(create_blank_image(), "ClearJpgLayer (blank 720x1472)")
    time.sleep(0.1)

    push_jpeg(create_deyloop_image(), "Deyloop's Driver image")

    usb.util.release_interface(dev, 0)
    usb.util.dispose_resources(dev)

def build_image_packet() -> bytes:
    """Builds the PushJpg (0x65) packet with the appended image bytes."""
    jpg_bytes = create_deyloop_image()
    header = build_base_cmd(0x65)
    
    # Image file size (Big-endian, 4 bytes at offset 8)
    struct.pack_into('>I', header, 8, len(jpg_bytes))
    
    encrypted_header = encrypt_header(header)
    return encrypted_header + jpg_bytes

if __name__ == "__main__":
    push_to_lcd()

