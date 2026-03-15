# Lian Li Lancool 207 LCD Driver

A Linux driver for the Lian Li Lancool 207 Digital LCD screen. This project enables displaying system stats (CPU, GPU, RAM, Network) on the LCD panel via USB.

## Features

- Real-time CPU and GPU usage monitoring
- Temperature tracking for CPU and GPU
- RAM usage display
- Network I/O statistics
- 3-minute historical graphs
- Custom background image support

## Requirements

- Python 3.12+
- USB access to the Lian Li Lancool 207 Digital device
- NVIDIA GPU with `nvidia-smi` (for GPU stats)

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
