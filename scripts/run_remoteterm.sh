#!/usr/bin/env bash
# Run RemoteTerm (ensures project root is on PYTHONPATH).
# Usage: ./scripts/run_remoteterm.sh [uvicorn args...]
# Example: ./scripts/run_remoteterm.sh --host 0.0.0.0 --port 8000

set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"
exec uvicorn app.main:app "$@"
