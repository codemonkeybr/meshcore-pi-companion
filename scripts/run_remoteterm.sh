#!/usr/bin/env bash
# Run RemoteTerm (ensures project root is on PYTHONPATH).
# This script only starts the backend API; frontend build is handled
# separately (e.g. via scripts/install_remoterm_pi.sh).
#
# Usage:
#   ./scripts/run_remoterm.sh [options] [uvicorn args...]
#
# Options:
#   --debug, -d    Set MESHCORE_LOG_LEVEL=DEBUG (default: INFO)
#
# Examples:
#   ./scripts/run_remoterm.sh --host 0.0.0.0 --port 8000
#   ./scripts/run_remoterm.sh --debug --host 0.0.0.0 --port 8000

set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"

UV_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --debug|-d)
      export MESHCORE_LOG_LEVEL=DEBUG
      shift
      ;;
    *)
      UV_ARGS+=("$1")
      shift
      ;;
  esac
done

exec uvicorn app.main:app "${UV_ARGS[@]}"
