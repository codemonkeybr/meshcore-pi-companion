#!/usr/bin/env bash
# One-time (or occasional) install/setup script for RemoteTerm on a Pi.
# - Creates a Python venv (if missing) and installs backend deps (with SPI extras)
# - Optionally builds the frontend if npm is available
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
if [ -f frontend/dist/index.html ]; then
  echo "frontend/dist/index.html already exists; skipping frontend build."
else
  if command -v npm >/dev/null 2>&1; then
    echo "npm detected; installing frontend deps (low-memory mode: 1 connection, 768MB heap)..."
    # Limit Node heap and one connection at a time to avoid OOM on Pi
    export NODE_OPTIONS="${NODE_OPTIONS:-} --max-old-space-size=768"
    if (cd frontend && npm install --maxsockets 1 --prefer-offline --no-audit --no-fund && npm run build); then
      echo "Frontend build complete."
    else
      echo "Frontend build failed (if you saw 'Killed', the Pi ran out of memory)."
      echo "Add swap and re-run, or build frontend on another machine and copy frontend/dist/ here."
    fi
  else
    echo "npm not found; skipping frontend build."
    echo "You can either:"
    echo "  - Install Node.js/npm and run: (cd frontend && npm install && npm run build)"
    echo "  - Or build the frontend on another machine and copy frontend/dist here."
  fi
fi

echo
echo "== Done =="
echo "Backend deps are installed in $VENV_DIR."
echo "Start the server with, for example:"
echo "  ./scripts/run_remoterm.sh --host 0.0.0.0 --port 8000"

