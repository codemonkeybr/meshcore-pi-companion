#!/usr/bin/env bash
# Lightweight dev install on Raspberry Pi: venv + ".[spi]" + optional local frontend zip.
# For production (systemd, SPI boot config, USB vs SPI, uninstall), use:
#   sudo ./scripts/manage_remoterm.sh
#
# - Creates a Python venv (if missing) and installs backend deps (with SPI extras)
# - If frontend/frontend-dist.zip exists, extracts it to frontend/dist (no download)
#
# Usage:
#   chmod +x scripts/install_remoteterm_pi.sh
#   ./scripts/install_remoteterm_pi.sh [--verbose|-v] [--no-spi]
#
# Options:
#   --verbose, -v  Show full pip output (useful for diagnosing build failures)
#   --no-spi       Skip SPI extras (for testing on non-Pi hardware)
#
# SPI wizard (writes data/config.yaml by default):
#   uv run python -m app.setup_cli
#
# Then start the app with:
#   ./scripts/run_remoteterm.sh --host 0.0.0.0 --port 8000

set -e

cd "$(dirname "$0")/.."
ROOT_DIR="$(pwd)"

VERBOSE=0
INSTALL_EXTRAS=".[spi]"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --verbose|-v) VERBOSE=1; shift ;;
    --no-spi) INSTALL_EXTRAS="."; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

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

echo "Activating virtualenv and installing Python dependencies..."
echo "  Extras: $INSTALL_EXTRAS"
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

PIP_FLAGS=(--no-cache-dir)
if [ "$VERBOSE" -eq 1 ]; then
  PIP_FLAGS+=(-v)
fi

pip install "${PIP_FLAGS[@]}" --upgrade pip
pip install "${PIP_FLAGS[@]}" "$INSTALL_EXTRAS"

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
echo "== Post-install verification =="
FAILED=0
for mod in uvicorn fastapi pydantic aiosqlite; do
  if "$VENV_DIR/bin/python" -c "import $mod" 2>/dev/null; then
    echo "  ✓ $mod"
  else
    echo "  ✗ $mod MISSING"
    FAILED=1
  fi
done

if [ "$FAILED" -eq 1 ]; then
  echo
  echo "ERROR: Some core dependencies are missing. The install likely failed."
  echo "Re-run with --verbose to see detailed pip output."
  exit 1
fi

echo
echo "== Done =="
echo "Backend deps are installed in $VENV_DIR."
echo "Start the server with, for example:"
echo "  ./scripts/run_remoterm.sh --host 0.0.0.0 --port 8000"
