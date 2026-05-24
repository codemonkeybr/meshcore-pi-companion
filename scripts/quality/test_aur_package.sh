#!/usr/bin/env bash
set -euo pipefail

# test_aur_package.sh — Build the AUR package in one Arch container, then
# install and run it in a clean Arch container with port 8000 exposed.
#
# Usage:
#   ./scripts/quality/test_aur_package.sh [--port PORT]
#
# The script streams application logs until you Ctrl-C.

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT=8000
if [ "${1:-}" = "--port" ]; then PORT="${2:-8000}"; fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ARTIFACT_DIR="$(mktemp -d)"
INSTALL_CONTAINER="remoteterm-aur-test-$$"

cleanup() {
    echo
    echo -e "${YELLOW}Cleaning up...${NC}"
    docker rm -f "$INSTALL_CONTAINER" 2>/dev/null || true
    rm -rf "$ARTIFACT_DIR"
    echo -e "${GREEN}Done.${NC}"
}
trap cleanup EXIT

# ── Phase 1: Build ────────────────────────────────────────────────────────────

echo -e "${BOLD}=== Phase 1: Build AUR package ===${NC}"

docker run --rm \
    -v "$REPO_ROOT/pkg/aur:/pkg:ro" \
    -v "$ARTIFACT_DIR:/out" \
    archlinux:latest bash -c '
set -euo pipefail

pacman -Syu --noconfirm base-devel git curl >/dev/null 2>&1
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
export PATH="$HOME/.local/bin:$PATH"
pacman -S --noconfirm nodejs npm >/dev/null 2>&1

useradd -m builder
echo "builder ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

BUILD_DIR=/home/builder/build
mkdir -p "$BUILD_DIR"
cp /pkg/PKGBUILD /pkg/remoteterm-meshcore.install \
   /pkg/remoteterm-meshcore.service /pkg/remoteterm-meshcore.sysusers \
   /pkg/remoteterm-meshcore.tmpfiles /pkg/remoteterm.env "$BUILD_DIR/"
chown -R builder:builder "$BUILD_DIR"

echo "Building package..."
su builder -c "export PATH=\"$HOME/.local/bin:\$PATH\" && cd $BUILD_DIR && makepkg -sf --noconfirm" 2>&1

cp "$BUILD_DIR"/remoteterm-meshcore-*.pkg.tar.zst /out/
echo "Package artifact copied to /out/"
ls -lh /out/*.pkg.tar.zst
'

PKG_FILE="$(ls "$ARTIFACT_DIR"/*.pkg.tar.zst 2>/dev/null | head -1)"
if [ -z "$PKG_FILE" ]; then
    echo -e "${RED}Build failed — no .pkg.tar.zst produced${NC}"
    exit 1
fi

echo -e "${GREEN}Built: $(basename "$PKG_FILE") ($(du -h "$PKG_FILE" | cut -f1))${NC}"
echo

# ── Phase 2: Install and run ─────────────────────────────────────────────────

echo -e "${BOLD}=== Phase 2: Install and run ===${NC}"

docker run -d \
    --name "$INSTALL_CONTAINER" \
    -p "$PORT:8000" \
    -v "$ARTIFACT_DIR:/pkg:ro" \
    archlinux:latest bash -c '
set -euo pipefail

# Install the package (sysusers.d creates the remoteterm user, tmpfiles.d creates the data dir)
pacman -Syu --noconfirm >/dev/null 2>&1
pacman -U --noconfirm /pkg/*.pkg.tar.zst

# In a container there is no systemd to trigger sysusers/tmpfiles automatically,
# so run them manually.
systemd-sysusers
systemd-tmpfiles --create

echo "============================================"
echo " RemoteTerm installed — starting server"
echo "============================================"

# Run as the remoteterm service user, matching the systemd unit
exec su -s /bin/bash remoteterm -c "cd /opt/remoteterm-meshcore && exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"
' >/dev/null

echo -e "${CYAN}Container:${NC} $INSTALL_CONTAINER"
echo -e "${CYAN}Listening:${NC} http://localhost:$PORT"
echo -e "${CYAN}Health:   ${NC} http://localhost:$PORT/api/health"
echo
echo -e "${YELLOW}Streaming logs (Ctrl-C to stop and clean up)...${NC}"
echo

docker logs -f "$INSTALL_CONTAINER"
