#!/usr/bin/env bash
# Run RemoteTerm (ensures project root is on PYTHONPATH).
# This script only starts the backend API; frontend build is handled
# separately (e.g. via scripts/install_remoterm_pi.sh).
#
# Usage:
#   ./scripts/run_remoterm.sh [uvicorn args...]
# Example:
#   ./scripts/run_remoterm.sh --host 0.0.0.0 --port 8000

set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"

exec uvicorn app.main:app "$@"
