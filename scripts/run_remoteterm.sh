#!/usr/bin/env bash
# Run RemoteTerm (ensures project root is on PYTHONPATH).
# Usage: ./scripts/run_remoteterm.sh [options] [uvicorn args...]
#   --with-frontend   Build the frontend (npm install + npm run build) before starting.
# Example: ./scripts/run_remoteterm.sh --host 0.0.0.0 --port 8000
# Example: ./scripts/run_remoteterm.sh --with-frontend --host 0.0.0.0 --port 8000

set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"

build_frontend=false
args=()
for arg in "$@"; do
  if [ "$arg" = "--with-frontend" ]; then
    build_frontend=true
  else
    args+=( "$arg" )
  fi
done

if [ "$build_frontend" = true ]; then
  echo "Building frontend..."
  (cd frontend && npm install && npm run build)
  echo "Frontend build complete."
fi

exec uvicorn app.main:app "${args[@]}"
