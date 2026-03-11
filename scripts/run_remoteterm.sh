#!/usr/bin/env bash
# Run RemoteTerm (ensures project root is on PYTHONPATH).
# Usage: ./scripts/run_remoteterm.sh [options] [uvicorn args...]
#   --with-frontend   Serve the web UI: build frontend if missing (npm install + npm run build),
#                     or use existing frontend/dist (e.g. copied from another machine).
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
  if [ -f frontend/dist/index.html ]; then
    echo "Frontend already built (frontend/dist), skipping build."
  else
    if ! command -v npm >/dev/null 2>&1; then
      echo "Error: --with-frontend requires either:"
      echo "  1. npm (Node.js) to build here: sudo apt install nodejs npm"
      echo "  2. Or copy a built frontend to this machine: rsync -av frontend/dist/ pi:remoteterm/frontend/dist/"
      echo "Then run this script again with --with-frontend."
      exit 1
    fi
    echo "Building frontend..."
    (cd frontend && npm install && npm run build)
    echo "Frontend build complete."
  fi
fi

exec uvicorn app.main:app "${args[@]}"
