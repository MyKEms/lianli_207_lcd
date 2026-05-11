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
SERVICE_USER="${SUDO_USER:-$USER}"

if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == "root" ]]; then
    echo "[-] Could not determine non-root user (SUDO_USER empty). Re-run via 'sudo' from your normal account."
    exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "[-] User '$SERVICE_USER' does not exist."
    exit 1
fi

if ! getent group plugdev >/dev/null; then
    echo "[*] Creating 'plugdev' group..."
    groupadd --system plugdev
fi

if ! id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx plugdev; then
    echo "[*] Adding $SERVICE_USER to 'plugdev' group (relogin required for shell sessions; service uses SupplementaryGroups so it works immediately)"
    usermod -aG plugdev "$SERVICE_USER"
fi

echo "[*] Creating venv at $UV_VENV..."
"$UV" venv "$UV_VENV" --python 3.12

echo "[*] Installing lianli-lcd-driver..."
"$UV" pip install --python "$UV_VENV/bin/python" .

echo "[*] Installing systemd service (user=$SERVICE_USER)..."
sed -e "s|__BACKGROUND_IMAGE__|${BACKGROUND}|g" \
    -e "s|__SERVICE_USER__|${SERVICE_USER}|g" \
    systemd/lcd-driver.service.template > "$SERVICE_FILE"

echo "[*] Installing udev rule (group=plugdev, mode=0660 — no world-RW)..."
cat > /etc/udev/rules.d/99-lianli-lcd.rules << EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="1cbe", ATTRS{idProduct}=="a065", GROUP="plugdev", MODE="0660", TAG+="uaccess"
EOF
udevadm control --reload-rules
udevadm trigger

echo "[*] Installing tmpfiles.d snippet for RAPL readability..."
# /sys/class/powercap/intel-rapl:0/energy_uj is root-only (0400) since
# kernel ~5.10 as part of the CVE-2020-8694 Platypus side-channel
# mitigation. Reading it at 1 Hz from a single-user box is far below the
# attack threshold (Platypus needed >100 Hz of fine-grained sampling
# across multiple workloads). Relax to 0444 so the hardened lcd-driver
# (running as non-root) can compute CPU package power for the System
# Power readout. AMD path is also covered if present.
cat > /etc/tmpfiles.d/99-lianli-lcd-rapl.conf << 'EOF'
# Set by lianli-lcd installer — relax CVE-2020-8694 mitigation for compute-power monitoring
z /sys/class/powercap/intel-rapl:0/energy_uj 0444 - - -
z /sys/class/powercap/amd-energy:0/energy_uj 0444 - - -
EOF
# Apply right now (paths missing on systems without that hw type are silently skipped)
systemd-tmpfiles --create /etc/tmpfiles.d/99-lianli-lcd-rapl.conf 2>/dev/null || true

echo "[*] Enabling and starting service..."
systemctl daemon-reload
systemctl enable --now lcd-driver

echo ""
echo "[+] Done. Check status with: journalctl -u lcd-driver -f"

