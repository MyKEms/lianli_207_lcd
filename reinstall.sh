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
    echo "Run as root: sudo ./reinstall.sh"
    exit 1
fi

UV_VENV="/opt/lianli-lcd"

echo "[*] Reinstalling lianli-lcd-driver into venv..."
"$UV" pip install --python "$UV_VENV/bin/python" .

echo "[*] Restarting service..."
systemctl restart lcd-driver

echo ""
echo "[+] Done. Check status with: journalctl -u lcd-driver -f"
