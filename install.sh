#!/usr/bin/env bash
set -e

UV="$(which uv 2>/dev/null || echo "")"
if [[ -z "$UV" ]]; then
    for candidate in \
        "/usr/local/bin/uv" \
        "/usr/bin/uv" \
        "/home/deyloop/.local/bin/uv" \
        "$HOME/.local/bin/uv" \
        "$HOME/.cargo/bin/uv"
    do
        if [[ -x "$candidate" ]]; then
            UV="$candidate"
            break
        fi
    done
fi

if [[ -z "$UV" ]]; then
    echo "[-] uv not found."
    exit 1
fi

echo "[*] Using uv at: $UV"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./install.sh <background_image_path>"
    exit 1
fi

if [[ -z "$1" ]]; then
    echo "Usage: sudo ./install.sh <background_image_path>"
    exit 1
fi

BACKGROUND="$1"
UV_VENV="/opt/lianli-lcd"
SERVICE_FILE="/etc/systemd/system/lcd-driver.service"

echo "[*] Creating venv at $UV_VENV..."
"$UV" venv "$UV_VENV" --python 3.12

echo "[*] Installing lianli-lcd-driver..."
"$UV" pip install --python "$UV_VENV/bin/python" .

echo "[*] Installing systemd service..."
sed "s|__BACKGROUND_IMAGE__|${BACKGROUND}|g" \
    systemd/lcd-driver.service.template > "$SERVICE_FILE"

echo "[*] Installing udev rule..."
cat > /etc/udev/rules.d/99-lianli-lcd.rules << EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="1cbe", ATTRS{idProduct}=="a065", MODE="0666"
EOF
udevadm control --reload-rules
udevadm trigger

echo "[*] Enabling and starting service..."
systemctl daemon-reload
systemctl enable --now lcd-driver

echo ""
echo "[+] Done. Check status with: journalctl -u lcd-driver -f"

