#!/usr/bin/env bash
set -e

# developer perogative ;D
if command -v enablenvm >/dev/null 2>&1; then
    enablenvm >/dev/null 2>&1 || true
fi


# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo -e "${YELLOW}=== RemoteTerm Quality Checks ===${NC}"
echo

# --- Phase 1: Lint & Format ---

echo -e "${YELLOW}=== Phase 1: Lint & Format ===${NC}"

echo -e "${BLUE}[backend lint]${NC} Running ruff check + format..."
cd "$SCRIPT_DIR"
uv run ruff check app/ tests/ --fix
uv run ruff format app/ tests/
echo -e "${GREEN}[backend lint]${NC} Passed!"

echo -e "${BLUE}[frontend lint]${NC} Running eslint + prettier..."
cd "$SCRIPT_DIR/frontend"
npm run lint:fix
npm run format
echo -e "${GREEN}[frontend lint]${NC} Passed!"

echo -e "${GREEN}=== Phase 1 complete ===${NC}"
echo

# --- Phase 2: Typecheck, Tests & Build ---

echo -e "${YELLOW}=== Phase 2: Typecheck, Tests & Build ===${NC}"

echo -e "${BLUE}[pyright]${NC} Running type check..."
cd "$SCRIPT_DIR"
uv run pyright app/
echo -e "${GREEN}[pyright]${NC} Passed!"

echo -e "${BLUE}[pytest]${NC} Running backend tests..."
cd "$SCRIPT_DIR"
PYTHONPATH=. uv run pytest tests/ -v
echo -e "${GREEN}[pytest]${NC} Passed!"

echo -e "${BLUE}[frontend]${NC} Running tests + build..."
cd "$SCRIPT_DIR/frontend"
npm run test:run
npm run build
echo -e "${GREEN}[frontend]${NC} Passed!"

echo -e "${GREEN}=== Phase 2 complete ===${NC}"
echo

echo -e "${GREEN}=== All quality checks passed! ===${NC}"
