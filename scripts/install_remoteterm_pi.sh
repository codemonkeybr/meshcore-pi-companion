#!/usr/bin/env bash
# One-time (or occasional) install/setup script for RemoteTerm on a Pi.
# - Creates a Python venv (if missing) and installs backend deps (with SPI extras)

# - If frontend/frontend-dist.zip exists, extracts it to frontend/dist (no download)
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
  echo "frontend/dist/index.html already exists; skipping frontend."
elif [ -f frontend/frontend-dist.zip ]; then
  echo "Found frontend/frontend-dist.zip; extracting to frontend/dist..."
  mkdir -p frontend/dist
  if (cd frontend/dist && unzip -o -q ../frontend-dist.zip); then
    echo "Frontend extracted to frontend/dist."
  else
    echo "Unzip failed. Check that frontend/frontend-dist.zip is valid."
  fi
else
  echo "No frontend/dist and no frontend/frontend-dist.zip."
  echo "Place frontend-dist.zip in the frontend/ folder and re-run, or build/copy frontend/dist manually."
fi

echo
echo "== Done =="
echo "Backend deps are installed in $VENV_DIR."
echo "Start the server with, for example:"
echo "  ./scripts/run_remoterm.sh --host 0.0.0.0 --port 8000"
