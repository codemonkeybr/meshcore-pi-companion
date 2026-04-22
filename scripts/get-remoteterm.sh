#!/usr/bin/env bash
# RemoteTerm bootstrap installer.
#
# Downloads the RemoteTerm backend release artifact from GitHub Releases and
# launches the interactive installer (manage_remoterm.sh).
#
# IMPORTANT: run with bash <(curl ...) to preserve the TTY required by the interactive menus:
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/codemonkeybr/meshcore-pi-companion/main/scripts/get-remoteterm.sh)
#
# Environment overrides:
#   RELEASE_VERSION=latest          Release tag to install (default: latest)
#                                   Examples: "latest", "v3.2.0"
#   REMOTETERM_REPO=codemonkeybr/meshcore-pi-companion
#                                   GitHub repo (owner/name) hosting the releases
#   ALLOW_NON_PI=1                  Skip Raspberry Pi check (for testing only)
#   FRONTEND_RELEASE_URL=...        Override prebuilt frontend zip URL

set -euo pipefail

REPO="${REMOTETERM_REPO:-codemonkeybr/meshcore-pi-companion}"
RELEASE_VERSION="${RELEASE_VERSION:-latest}"
GITHUB_API="https://api.github.com"
GITHUB_RELEASES="https://github.com/${REPO}/releases"
TMP_DIR="$(mktemp -d /tmp/remoteterm-install-XXXXXX)"

# Clean up temp dir on error; exec replaces the process so this won't fire on success.
trap 'echo "Bootstrap failed; cleaning up..." >&2; rm -rf "$TMP_DIR"' ERR

echo "=== RemoteTerm Bootstrap ==="
echo "Repo    : $REPO"
echo "Version : $RELEASE_VERSION"
echo "Temp    : $TMP_DIR"
echo ""

if ! command -v curl &>/dev/null && ! command -v wget &>/dev/null; then
  echo "Error: need curl or wget to download the RemoteTerm release." >&2
  exit 1
fi

# http_fetch URL OUTFILE -- fetch URL to OUTFILE with progress (or silent if no TTY).
http_fetch() {
  local url="$1" out="$2"
  if command -v curl &>/dev/null; then
    if [ -t 1 ]; then
      curl -fL --progress-bar -o "$out" "$url"
    else
      curl -fsSL -o "$out" "$url"
    fi
  else
    wget -q --show-progress -O "$out" "$url"
  fi
}

# http_get_text URL -- print URL body to stdout, silent (used for API queries).
http_get_text() {
  local url="$1"
  if command -v curl &>/dev/null; then
    curl -fsSL "$url"
  else
    wget -q -O- "$url"
  fi
}

resolve_version() {
  local version="$1"
  if [ "$version" != "latest" ]; then
    echo "$version"
    return
  fi
  local body
  if ! body="$(http_get_text "${GITHUB_API}/repos/${REPO}/releases/latest")"; then
    echo "Error: failed to query GitHub Releases API for the latest version." >&2
    echo "Check network, GitHub availability, and API rate limits, or set RELEASE_VERSION=vX.Y.Z explicitly." >&2
    exit 1
  fi
  # Minimal tag_name extraction without jq: take first "tag_name": "..." line.
  local tag
  tag="$(printf '%s' "$body" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  if [ -z "$tag" ]; then
    echo "Error: could not parse tag_name from GitHub API response." >&2
    exit 1
  fi
  echo "$tag"
}

echo "Resolving release version..."
TAG="$(resolve_version "$RELEASE_VERSION")"
# Strip leading "v" for the artifact filename (remoteterm-backend-3.2.0.tar.gz).
SEMVER="${TAG#v}"
ARTIFACT="remoteterm-backend-${SEMVER}.tar.gz"
CHECKSUM="${ARTIFACT}.sha256"
ARTIFACT_URL="${GITHUB_RELEASES}/download/${TAG}/${ARTIFACT}"
CHECKSUM_URL="${GITHUB_RELEASES}/download/${TAG}/${CHECKSUM}"

echo "Release : $TAG"
echo "Asset   : $ARTIFACT"
echo ""

echo "Downloading release artifact..."
if ! http_fetch "$ARTIFACT_URL" "$TMP_DIR/$ARTIFACT"; then
  echo "Error: failed to download $ARTIFACT_URL" >&2
  echo "Possible causes: invalid version tag, network failure, GitHub rate limit." >&2
  exit 1
fi

echo "Downloading checksum..."
if http_fetch "$CHECKSUM_URL" "$TMP_DIR/$CHECKSUM" 2>/dev/null; then
  echo "Verifying SHA256 checksum..."
  (cd "$TMP_DIR" && sha256sum -c "$CHECKSUM") || {
    echo "Error: checksum verification failed for $ARTIFACT." >&2
    exit 1
  }
else
  echo "Warning: no checksum file at $CHECKSUM_URL; skipping integrity verification." >&2
fi

echo "Extracting release..."
mkdir -p "$TMP_DIR/src"
tar -xzf "$TMP_DIR/$ARTIFACT" -C "$TMP_DIR/src"

MANAGER="$TMP_DIR/src/scripts/manage_remoterm.sh"
if [ ! -f "$MANAGER" ]; then
  echo "Error: $MANAGER missing after extraction — release artifact is malformed." >&2
  exit 1
fi
chmod +x "$MANAGER"

echo ""
echo "Release extracted. Launching installer..."
echo "(Source files are in $TMP_DIR/src — safe to delete after install.)"
echo ""

# exec replaces this process; the TMP_DIR files stay on disk for the installer to use.
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo \
    ALLOW_NON_PI="${ALLOW_NON_PI:-0}" \
    FRONTEND_RELEASE_URL="${FRONTEND_RELEASE_URL:-}" \
    RELEASE_VERSION="$TAG" \
    bash "$MANAGER" install
else
  exec \
    ALLOW_NON_PI="${ALLOW_NON_PI:-0}" \
    FRONTEND_RELEASE_URL="${FRONTEND_RELEASE_URL:-}" \
    RELEASE_VERSION="$TAG" \
    bash "$MANAGER" install
fi
