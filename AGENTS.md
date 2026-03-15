# AGENTS.md - Lian Li Lancool 207 LCD Driver

## Project Overview

Linux driver for the Lian Li Lancool 207 Digital LCD screen. This Python 3.12+ project communicates with the LCD via USB, renders system stats (CPU/GPU/RAM/Network), and pushes images to the display.

## Build Commands

### Installation
```bash
# Using uv (recommended)
uv sync

# Using pip
pip install -e .
```

### System Installation (requires root)
```bash
# Install with systemd service (requires background image path)
sudo ./install.sh /path/to/background.jpg

# Reinstall/reload after changes
sudo ./reinstall.sh

# Uninstall completely
sudo ./uninstall.sh

# Check service status
journalctl -u lcd-driver -f
```

### Running the driver (development)
```bash
lcd-driver <background_image>
```

### Type checking (only tool configured)
```bash
# Run basedpyright
basedpyright lcd_driver/

# Or using pyright directly if installed
pyright lcd_driver/
```

### Building the package
```bash
python -m build
```

### Running a single test
No tests exist yet. To add tests, use pytest:
```bash
pytest tests/                    # Run all tests
pytest tests/test_file.py        # Run specific test file
pytest tests/test_file.py::test_function_name  # Run specific test
```

## Code Style Guidelines

### General
- Python 3.12+ required
- Follow PEP 8 with 100 character line limit
- Use type hints for all function signatures and variables when beneficial
- Run `basedpyright` before committing - it must pass without errors

### Imports
- Standard library imports first
- Third-party imports second
- Local imports last
- Use absolute imports (e.g., `from lcd_driver import foo`)
- Separate import groups with a single blank line

```python
# Correct
import os
import sys
import struct
import time

import usb.core
import usb.util
from PIL import Image, ImageDraw, ImageFont

from lcd_driver import utils
```

### Naming Conventions
- Use `snake_case` for functions, variables, and methods
- Use `SCREAMING_SNAKE_CASE` for constants
- Use `PascalCase` for classes
- Use descriptive names - avoid single letters except in tight loops

### Functions
- Keep functions focused and under 100 lines
- Add type hints for parameters and return values
- Use docstrings for public APIs (Google style preferred)
- Prefer early returns over deeply nested conditionals

```python
def get_timestamp() -> int:
    """Get milliseconds since app start, truncated to 32-bit unsigned."""
    return int((time.time() - APP_START) * 1000) & 0xFFFFFFFF
```

### Error Handling
- Catch specific exceptions rather than broad `Exception`
- Log errors with appropriate level (debug for expected failures, warning/error for unexpected)
- Use meaningful error messages that include context

```python
# Good - specific exception, informative message
try:
    out = subprocess.check_output(["nvidia-smi", ...], timeout=2).decode().strip()
except subprocess.TimeoutExpired as e:
    log.debug(f"nvidia-smi timed out: {e}")
    return {"util": 0, "temp": 0, "power": 0.0}
```

### USB/Driver Code
- Handle USB errors gracefully - device may be unplugged
- Always detach kernel drivers before claiming USB interface
- Use appropriate timeouts for USB operations
- Clean up resources in finally blocks or with context managers

### Image Processing
- Use PIL/Pillow for image manipulation
- Compress images to fit LCD constraints (max 512KB for JPEG)
- Rotate images correctly for the display orientation
- Use RGBA for overlays, RGB for backgrounds

### Logging
- Use the module-level logger: `log = logging.getLogger("lcd_driver")`
- Log levels: DEBUG for verbose info, INFO for normal operation, WARNING/ERROR for issues
- Include relevant data in log messages but avoid sensitive information
- Use f-strings for log messages

### Constants
- Define magic numbers as named constants at module level
- Use SCREAMING_SNAKE_CASE
- Document units and meanings in comments when not obvious

```python
VID         = 0x1cbe          # USB Vendor ID
PID         = 0xa065          # USB Product ID
DES_KEY     = b'slv3tuzx'     # Encryption key (8 bytes for DES)
W, H        = 720, 1472       # Display dimensions (landscape)
HISTORY_LEN = 180             # Number of samples (180 * 1s = 3 min)
```

### Data Structures
- Use `collections.deque` with `maxlen` for rolling history buffers
- Use typed dicts or dataclasses for related data
- Prefer immutability where practical

### Testing (when added)
- Use pytest as the test framework
- Place tests in `tests/` directory
- Mock USB device interactions
- Test image generation functions with fixture images

## Project Structure

```
.
├── lcd_driver/
│   ├── __init__.py
│   └── main.py           # Main driver code
├── pyproject.toml        # Project configuration
├── README.md
├── install.sh            # Installation script (creates venv at /opt/lianli-lcd)
├── reinstall.sh          # Reinstall and restart systemd service
├── uninstall.sh          # Remove systemd service and venv
└── systemd/
    └── lcd-driver.service.template  # Systemd service template
```

## System Installation Details

The install script performs the following:
1. Creates a venv at `/opt/lianli-lcd` using Python 3.12
2. Installs the package into the venv
3. Installs systemd service at `/etc/systemd/system/lcd-driver.service`
4. Installs udev rule at `/etc/udev/rules.d/99-lianli-lcd.rules` for USB access
5. Enables and starts the service

The udev rule grants non-root access to the USB device:
- Vendor ID: `0x1cbe`
- Product ID: `0xa065`

To debug, check the service logs:
```bash
journalctl -u lcd-driver -f
```

## Dependencies

- `pyusb` - USB communication
- `pillow` - Image processing
- `pycryptodome` - DES encryption for LCD protocol
- `psutil` - System stats (CPU, GPU, RAM, network)

## Development Dependencies

- `basedpyright` - Static type checking
