#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "Starting E2E tests..."
cd "$REPO_ROOT/tests/e2e"
npx playwright test "$@"
