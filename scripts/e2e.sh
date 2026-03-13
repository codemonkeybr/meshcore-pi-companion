#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "Starting E2E tests..."
cd "$SCRIPT_DIR/tests/e2e"
npx playwright test "$@"
