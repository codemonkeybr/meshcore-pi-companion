#!/usr/bin/env bash
# RemoteTerm bootstrap installer.
#
# Downloads the RemoteTerm source and launches the interactive installer (manage_remoterm.sh).
#
# IMPORTANT: run with bash <(curl ...) to preserve the TTY required by the interactive menus:
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/codemonkeybr/meshcore-pi-companion/main/scripts/get-remoteterm.sh)
#
# Environment overrides:
#   REMOTETERM_BRANCH=main          Git branch to clone (default: main)
#   ALLOW_NON_PI=1                  Skip Raspberry Pi check (for testing only)
#   FRONTEND_RELEASE_URL=...        Override prebuilt frontend zip URL

set -euo pipefail

REPO_URL="https://github.com/codemonkeybr/meshcore-pi-companion"
BRANCH="${REMOTETERM_BRANCH:-feature/issue-13-self-install}"
TMP_DIR="$(mktemp -d /tmp/remoteterm-install-XXXXXX)"

# Clean up temp dir on error; exec replaces the process so this won't fire on success.
trap 'echo "Bootstrap failed; cleaning up..." >&2; rm -rf "$TMP_DIR"' ERR

echo "=== RemoteTerm Bootstrap ==="
echo "Branch : $BRANCH"
echo "Temp   : $TMP_DIR"
echo ""

# Download source
if command -v git &>/dev/null; then
  echo "Cloning $REPO_URL (branch: $BRANCH)..."
  git clone --depth=1 --branch "$BRANCH" "$REPO_URL" "$TMP_DIR/src"
elif command -v curl &>/dev/null; then
  echo "git not found — downloading tarball via curl..."
  curl -fsSL "$REPO_URL/archive/refs/heads/${BRANCH}.tar.gz" \
    | tar -xz -C "$TMP_DIR"
  mv "$TMP_DIR"/meshcore-pi-companion-* "$TMP_DIR/src"
elif command -v wget &>/dev/null; then
  echo "git not found — downloading tarball via wget..."
  wget -q -O- "$REPO_URL/archive/refs/heads/${BRANCH}.tar.gz" \
    | tar -xz -C "$TMP_DIR"
  mv "$TMP_DIR"/meshcore-pi-companion-* "$TMP_DIR/src"
else
  echo "Error: need git, curl, or wget to download RemoteTerm." >&2
  exit 1
fi

MANAGER="$TMP_DIR/src/scripts/manage_remoterm.sh"
chmod +x "$MANAGER"

echo ""
echo "Source downloaded. Launching installer..."
echo "(Source files are in $TMP_DIR/src — safe to delete after install.)"
echo ""

# exec replaces this process; the TMP_DIR files stay on disk for the installer to use.
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo \
    ALLOW_NON_PI="${ALLOW_NON_PI:-0}" \
    FRONTEND_RELEASE_URL="${FRONTEND_RELEASE_URL:-}" \
    bash "$MANAGER" install
else
  exec \
    ALLOW_NON_PI="${ALLOW_NON_PI:-0}" \
    FRONTEND_RELEASE_URL="${FRONTEND_RELEASE_URL:-}" \
    bash "$MANAGER" install
fi
