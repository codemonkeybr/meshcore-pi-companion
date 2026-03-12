#!/usr/bin/env bash
# One-time (or occasional) install/setup script for RemoteTerm on a Pi.
# - Creates a Python venv (if missing) and installs backend deps (with SPI extras)
# - Tries to download prebuilt frontend from GitHub release; falls back to npm build if missing
#
# Usage:
#   chmod +x scripts/install_remoterm_pi.sh   # once
#   ./scripts/install_remoterm_pi.sh          # run from project root
#
# After this, start the app with:
#   ./scripts/run_remoterm.sh --host 0.0.0.0 --port 8000

set -e

cd "$(dirname "$0")/.."
ROOT_DIR="$(pwd)"

echo "== RemoteTerm install/setup =="
echo "Project root: $ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

echo
echo "== Python / venv =="
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: $PYTHON_BIN not found. Install Python 3 (e.g. sudo apt install python3 python3-venv)."
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtualenv in $VENV_DIR..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
else
  echo "Virtualenv $VENV_DIR already exists, reusing."
fi

echo "Activating virtualenv and installing Python dependencies (including [spi])..."
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install ".[spi]"

echo
echo "== Frontend (optional) =="
FRONTEND_RELEASE_URL="${FRONTEND_RELEASE_URL:-https://github.com/codemonkeybr/remote-terminal-fork/releases/download/frontend-latest/frontend-dist.zip}"
if [ -f frontend/dist/index.html ]; then
  echo "frontend/dist/index.html already exists; skipping frontend."
else
  echo "Trying to download prebuilt frontend from GitHub..."
  try_npm_build=0
  if command -v curl >/dev/null 2>&1; then
    if curl -sfL --connect-timeout 30 --max-time 120 --retry 2 --retry-delay 5 \
       -A "Mozilla/5.0 (compatible; Linux; RemoteTerm-Pi/1.0)" \
       -o /tmp/frontend-dist.zip "$FRONTEND_RELEASE_URL"; then
      mkdir -p frontend/dist
      if (cd frontend/dist && unzip -o -q /tmp/frontend-dist.zip); then
        rm -f /tmp/frontend-dist.zip
        echo "Prebuilt frontend downloaded and extracted to frontend/dist."
      else
        rm -f /tmp/frontend-dist.zip
        echo "Unzip failed; falling back to local build or manual copy."
        try_npm_build=1
      fi
    else
      echo "Download failed (no release yet or no network); falling back to local build or manual copy."
      try_npm_build=1
    fi
  elif command -v wget >/dev/null 2>&1; then
    if wget -q --timeout=30 --tries=2 -O /tmp/frontend-dist.zip \
       --user-agent="Mozilla/5.0 (compatible; Linux; RemoteTerm-Pi/1.0)" \
       "$FRONTEND_RELEASE_URL"; then
      mkdir -p frontend/dist
      if (cd frontend/dist && unzip -o -q /tmp/frontend-dist.zip); then
        rm -f /tmp/frontend-dist.zip
        echo "Prebuilt frontend downloaded and extracted to frontend/dist."
      else
        rm -f /tmp/frontend-dist.zip
        echo "Unzip failed; falling back to local build or manual copy."
        try_npm_build=1
      fi
    else
      echo "wget failed; falling back to local build or manual copy."
      try_npm_build=1
    fi
  else
    echo "Neither curl nor wget found; skipping download."
    try_npm_build=1
  fi

  # Fallback: build frontend with npm when download failed or no curl/wget (keep this path for offline/slow Pi).
  if [ "$try_npm_build" = "1" ]; then
    if command -v npm >/dev/null 2>&1; then
      echo "npm detected; installing frontend deps (low-memory mode: 1 connection, 768MB heap)..."
      export NODE_OPTIONS="${NODE_OPTIONS:-} --max-old-space-size=768"
      if (cd frontend && npm install --maxsockets 1 --prefer-offline --no-audit --no-fund && npm run build); then
        echo "Frontend build complete."
      else
        echo "Frontend build failed (if you saw 'Killed', the Pi ran out of memory)."
        echo "Add swap and re-run, or copy frontend/dist from another machine or download from: $FRONTEND_RELEASE_URL"
      fi
    else
      echo "You can either:"
      echo "  - Install curl/wget and re-run this script to download the prebuilt frontend"
      echo "  - Install Node.js/npm and run: (cd frontend && npm install && npm run build)"
      echo "  - Download manually: $FRONTEND_RELEASE_URL and extract into frontend/dist/"
    fi
  fi
fi

echo
echo "== Done =="
echo "Backend deps are installed in $VENV_DIR."
echo "Start the server with, for example:"
echo "  ./scripts/run_remoterm.sh --host 0.0.0.0 --port 8000"
