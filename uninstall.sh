#!/usr/bin/env bash
set -e

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./uninstall.sh"
    exit 1
fi

echo "[*] Stopping and disabling service..."
systemctl disable --now lcd-driver || true

echo "[*] Removing service file..."
rm -f /etc/systemd/system/lcd-driver.service
systemctl daemon-reload

echo "[*] Removing udev rule..."
rm -f /etc/udev/rules.d/99-lianli-lcd.rules
udevadm control --reload-rules

echo "[*] Removing tmpfiles.d snippet (RAPL perms reset on next boot)..."
rm -f /etc/tmpfiles.d/99-lianli-lcd-rapl.conf

echo "[*] Uninstalling lianli-lcd-driver..."
rm -f /usr/local/bin/lcd-driver
rm -rf /opt/lianli-lcd

echo "[+] Done."

