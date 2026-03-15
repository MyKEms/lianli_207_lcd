# PROTOCOL.md - Lian Li Lancool 207 LCD Protocol

## USB Device

| Property | Value |
|----------|-------|
| Vendor ID | `0x1cbe` |
| Product ID | `0xa065` |
| Interface | 0 |
| Endpoints | Bulk IN (0x81), Bulk OUT (0x01) |

## Header Structure

Each command starts with a 500-byte header that gets encrypted.

### Base Header (500 bytes before encryption)

```
Offset  Size  Type    Description
─────────────────────────────────────────────
0       1     uint8   Command byte (see Command Types)
1       1     uint8   Unknown (usually 0x00)
2       1     uint8   Magic? (always 0x1A)
3       1     uint8   Magic? (always 0x6D)
4       4     uint32  Timestamp (ms since app start, little-endian)
8       492   ...     Command-specific data
```

### Encryption

- Algorithm: **DES-CBC**
- Key: `b'slv3tuzx'` (8 bytes)
- IV: `b'slv3tuzx'` (same as key)
- Padding: PKCS7
- Output: 512 bytes (first 504 bytes = encrypted data, bytes 510-511 = `0xA1 0x1A`)

```python
def encrypt_header(header_500: bytearray) -> bytes:
    padded    = pad(bytes(header_500), DES.block_size, style='pkcs7')
    cipher    = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_KEY)
    encrypted = cipher.encrypt(padded)

    result        = bytearray(512)
    result[0:504] = encrypted[:504]
    result[510]   = 0xA1
    result[511]   = 0x1A
    return bytes(result)
```

## Command Types

| Byte | Name | Description | Data Format |
|------|------|-------------|-------------|
| `0x0D` | Rotate | Set display rotation | `header[8] = rotation` (0=0°, 1=90°, etc.) |
| `0x33` | SyncClock | Set RTC time | `header[8:16]` = year(2), month, day, hour, min, sec, unk |
| `0x34` | StopClock | Stop RTC | No data |
| `0x65` | JPEG | Send JPEG image | `header[8:12]` = size (big-endian uint32), then raw JPEG |
| `0x66` | PNG | Send PNG overlay | `header[8:12]` = size (big-endian uint32), then raw PNG |
| `0x79` | StartPlay | Begin displaying sent image | No data |

### Rotate Command (0x0D)

```python
def build_rotate_cmd(rotation: int = 0) -> bytes:
    header = build_base_cmd(0x0D)
    header[8] = rotation
    return encrypt_header(header)
```

### Clock Commands (0x33 / 0x34)

```python
def build_clock_packet(is_stop: bool) -> bytes:
    cmd    = 0x34 if is_stop else 0x33
    header = build_base_cmd(cmd)
    if not is_stop:
        now        = datetime.now()
        header[8]  = (now.year >> 8) & 0xFF   # Year high byte
        header[9]  = now.year & 0xFF           # Year low byte
        header[10] = now.month
        header[11] = now.day
        header[12] = now.hour
        header[13] = now.minute
        header[14] = now.second
        header[15] = 2                         # Unknown
    return encrypt_header(header)
```

### Image Commands (0x65 / 0x66)

```python
def build_jpeg_packet(jpg_bytes: bytes) -> bytes:
    header = build_base_cmd(0x65)
    struct.pack_into('>I', header, 8, len(jpg_bytes))  # Big-endian size
    return encrypt_header(header) + jpg_bytes

def build_png_packet(png_bytes: bytes) -> bytes:
    header = build_base_cmd(0x66)
    struct.pack_into('>I', header, 8, len(png_bytes))
    return encrypt_header(header) + png_bytes
```

## Image Specifications

| Type | Format | Max Size | Orientation | Dimensions |
|------|--------|----------|-------------|------------|
| Background | JPEG | 512,000 bytes | Rotated -90° | 1472 × 720 → 720 × 1472 |
| Overlay | PNG | 512,000 bytes | Rotated -90° | 1472 × 720 → 720 × 1472 |
| Blank | PNG | N/A | Rotated -90° | 720 × 1472 (portrait) |

The display is physically 720×1472 (landscape input, rotated for portrait orientation).

## Data Transfer

### Chunking

Large payloads are split into 4096-byte chunks:

```python
def push_chunked(full_payload: bytes, label: str):
    total  = len(full_payload)
    chunks = (total + 4095) // 4096
    for i in range(0, total, 4096):
        chunk = full_payload[i:i + 4096]
        ep_out.write(chunk, timeout=5000)
    time.sleep(0.3)
    # Send StartPlay command
    send_and_read(encrypt_header(build_base_cmd(0x79)), "StartPlay")
```

- Chunk size: 4096 bytes
- Retries: 3 per chunk on timeout
- Inter-chunk delay: None explicitly
- Post-transfer delay: 300ms before StartPlay

### ACK Mechanism

After each command, the device returns a 512-byte ACK:

```python
def send_and_read(payload: bytes, name: str):
    ep_out.write(payload, timeout=200)
    try:
        ack = ep_in.read(512, timeout=200)
    except usb.core.USBError:
        pass  # No ACK is sometimes expected
```

## Initialization Sequence

The driver sends this sequence on startup:

1. **Rotate** (0x0D) — Set rotation to 0
2. **SyncClock** (0x33) — Send current time
3. **StopClock** (0x34) — Stop RTC
4. **Clear PNG** (0x66) — Send blank PNG overlay
5. **Clear JPEG** (0x65) — Send blank JPEG background
6. **Background** (0x65) — Send actual background image
7. **Stats Loop** (0x66 every 1s) — Send PNG overlays with live stats

Each command includes a 100ms delay except between clear and background (200ms).

## USB Communication Details

- **Interface**: 0
- **Endpoint OUT**: Bulk, address 0x01
- **Endpoint IN**: Bulk, address 0x81
- **Timeouts**:
  - Command write: 200ms
  - ACK read: 200ms
  - Chunk write: 5000ms
- **Kernel driver must be detached** before claiming interface
- **Configuration** must be set before use

## Error Handling

- USB timeouts trigger retry (up to 3 times per chunk)
- On failure, `clear_halt()` is called on the endpoint
- Device may be unplugged; handle `USBError` gracefully

## Constants

```python
VID         = 0x1cbe          # USB Vendor ID
PID         = 0xa065          # USB Product ID
DES_KEY     = b'slv3tuzx'     # Encryption key (8 bytes for DES)
W, H        = 720, 1472       # Display dimensions (landscape)
CHUNK_SIZE  = 4096            # USB transfer chunk size
MAX_IMAGE   = 512000          # Max image bytes
```
