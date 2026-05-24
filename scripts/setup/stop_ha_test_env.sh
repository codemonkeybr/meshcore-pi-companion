#!/usr/bin/env bash
# Stop and remove the HA + Mosquitto test containers.
# Optionally pass --clean to also wipe the HA config directory.
#
# Usage:
#   ./scripts/setup/stop_ha_test_env.sh          # stop containers only
#   ./scripts/setup/stop_ha_test_env.sh --clean   # stop + wipe ha_test_config

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HA_CONFIG="$REPO_ROOT/ha_test_config"

echo "==> Stopping test containers..."
docker rm -f ha-test-mosquitto 2>/dev/null && echo "    Removed ha-test-mosquitto" || echo "    ha-test-mosquitto not running"
docker rm -f ha-test-homeassistant 2>/dev/null && echo "    Removed ha-test-homeassistant" || echo "    ha-test-homeassistant not running"

if [ "${1:-}" = "--clean" ]; then
  echo "==> Wiping $HA_CONFIG..."
  sudo rm -rf "$HA_CONFIG"
  echo "    Done."
else
  echo ""
  echo "  HA config preserved at $HA_CONFIG"
  echo "  Run with --clean to remove it."
fi

echo "==> Done."
