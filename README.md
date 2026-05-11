# Lian Li Lancool 207 LCD Driver

A Linux driver for the Lian Li Lancool 207 Digital LCD screen. Displays CPU, GPU, and RAM stats on the front-panel LCD via USB.

## Features

- Real-time CPU usage, temperature, frequency, and (Intel/AMD) package power
- Real-time GPU usage, temperature, power draw, and VRAM via `nvidia-smi`
- RAM usage (used / total / percent)
- 3-minute rolling history graphs (CPU% / CPU°C / RAM% on the left, GPU% / GPU°C / VRAM% on the right)
- Custom background image (JPEG, rotated and resized to 720×1472)
- Title and GPU label overridable via `LCD_TITLE` and `LCD_GPU_LABEL` env vars (defaults: hostname and `nvidia-smi` detected GPU name)
- System power readout in the title strip = CPU package (RAPL) + GPU (`nvidia-smi power.draw`); add a fixed `LCD_BASELINE_WATTS=40` to the unit's `Environment=` if you want to fold in the unmeasurable rest (motherboard + RAM + fans + SSDs + PSU loss)

## Requirements

- Python 3.12+
- USB access to the Lian Li Lancool 207 Digital device (`VID 0x1cbe`, `PID 0xa065`)
- NVIDIA GPU with `nvidia-smi` in `PATH` (GPU panel falls back to zeros if absent)
- For CPU package power: Intel RAPL (`/sys/class/powercap/intel-rapl:0`) or AMD energy counter (`/sys/class/powercap/amd-energy:0`); optional, the bar just omits the wattage if neither is readable

## Installation

### Development

```bash
uv sync
```

### System-wide with systemd

```bash
sudo ./install.sh /path/to/background.jpg
```

## Usage

### Running the driver

```bash
lcd-driver <background_image>
```

### Checking status

```bash
journalctl -u lcd-driver -f
```

### Reinstalling after changes

```bash
sudo ./reinstall.sh
```

### Uninstalling

```bash
sudo ./uninstall.sh
```

## Documentation

- [PROTOCOL.md](PROTOCOL.md) - USB protocol documentation
- [AGENTS.md](AGENTS.md) - Development guidelines for AI agents

## Legal

This software is **not** affiliated with, endorsed, or supported by Lian Li or its affiliates.

This project is a reverse-engineered driver developed independently to bring Linux support to the Lian Li Lancool 207 Digital LCD screen, a product that was not originally intended to work on the Linux platform. The protocol was analyzed through reverse engineering of the Windows driver and USB traffic.

This software is provided in good faith to enable Linux users to utilize their hardware. Use at your own risk.

## License

MIT License - see [LICENSE.md](LICENSE.md)
