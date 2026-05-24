#!/usr/bin/env bash
set -euo pipefail

# run_aur_with_radio.sh — Install the published AUR package via yay in an Arch
# container with a real radio attached over serial.
#
# Usage:
#   ./scripts/quality/run_aur_with_radio.sh [--device PATH] [--port PORT]
#
# Defaults:
#   --device /dev/serial/by-id/usb-Heltec_HT-n5262_F423934AA2AB2A5E-if00
#   --port   8000

DEVICE="/dev/serial/by-id/usb-Heltec_HT-n5262_F423934AA2AB2A5E-if00"
PORT=8000

while [ "${1:-}" ]; do
  case "$1" in
    --device) DEVICE="$2"; shift 2 ;;
    --port)   PORT="$2"; shift 2 ;;
    *)        echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [ ! -e "$DEVICE" ]; then
  echo "Error: device $DEVICE not found" >&2
  exit 1
fi

CONTAINER="remoteterm-aur-radio-$$"

cleanup() {
  echo
  echo "Cleaning up..."
  docker rm -f "$CONTAINER" 2>/dev/null || true
  echo "Done."
}
trap cleanup EXIT

echo "=== Installing AUR package with radio ==="
echo "  Device: $DEVICE"
echo "  Port:   http://localhost:$PORT"
echo

docker run -it --rm \
  --name "$CONTAINER" \
  --device "$DEVICE:/dev/meshcore-radio" \
  -p "$PORT:8000" \
  archlinux:latest bash -c '
set -euo pipefail

echo "[1/3] Setting up yay..."
pacman -Syu --noconfirm base-devel git curl nodejs npm >/dev/null 2>&1
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1

# yay needs a non-root user
useradd -m builder
echo "builder ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

# Install yay
su builder -c "
  export PATH=\"\$HOME/.local/bin:\$PATH\"
  cd /tmp
  git clone https://aur.archlinux.org/yay-bin.git 2>&1
  cd yay-bin
  makepkg -si --noconfirm 2>&1
"

echo "[2/3] Installing remoteterm-meshcore from AUR..."
su builder -c "
  export PATH=\"\$HOME/.local/bin:\$PATH\"
  yay -S --noconfirm remoteterm-meshcore 2>&1
"

# Create user and data dir (no systemd PID 1 in container)
systemd-sysusers
systemd-tmpfiles --create

# Give the service user access to the serial device
chmod 666 /dev/meshcore-radio

echo "[3/3] Starting RemoteTerm..."
cd /opt/remoteterm-meshcore
exec su -s /bin/bash remoteterm -c "\
  MESHCORE_SERIAL_PORT=/dev/meshcore-radio \
  MESHCORE_DATABASE_PATH=/var/lib/remoteterm-meshcore/meshcore.db \
  exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"
'
