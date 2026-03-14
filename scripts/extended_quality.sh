#!/usr/bin/env bash
set -euo pipefail

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo -e "${YELLOW}=== Extended Quality Checks ===${NC}"
echo

echo -e "${BLUE}[all_quality]${NC} Running full lint, typecheck, unit tests, and builds..."
"$SCRIPT_DIR/scripts/all_quality.sh"
echo -e "${GREEN}[all_quality]${NC} Passed!"
echo

echo -e "${BLUE}[e2e]${NC} Running end-to-end tests..."
"$SCRIPT_DIR/scripts/e2e.sh" "$@"
echo -e "${GREEN}[e2e]${NC} Passed!"
echo

echo -e "${BLUE}[docker_ci]${NC} Running Docker frontend install/build matrix..."
"$SCRIPT_DIR/scripts/docker_ci.sh"
echo -e "${GREEN}[docker_ci]${NC} Passed!"
echo

echo -e "${GREEN}=== Extended quality checks passed! ===${NC}"
