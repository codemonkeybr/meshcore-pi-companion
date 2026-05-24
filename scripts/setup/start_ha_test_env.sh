#!/usr/bin/env bash
# Start a fresh Home Assistant + Mosquitto environment for testing the
# mqtt_ha fanout integration. Runs everything on the host network so
# RemoteTerm (running locally) can reach the broker at localhost:1883.
#
# Usage:
#   ./scripts/setup/start_ha_test_env.sh
#
# After this script completes:
#   1. HA is at http://localhost:8123 (login: dev / dev)
#   2. Mosquitto is at localhost:1883 (no auth)
#   3. HA's MQTT integration is configured and connected to Mosquitto
#
# Then in RemoteTerm:
#   Settings > Integrations > Add > Home Assistant
#   Broker Host: 127.0.0.1  Port: 1883
#   Select contacts/repeaters and save.
#
# To tear down:
#   ./scripts/setup/stop_ha_test_env.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HA_CONFIG="$REPO_ROOT/ha_test_config"
HA_CLIENT_ID="http://localhost:8123/"

ha_storage_has_domain() {
  local domain="$1"
  HA_STORAGE_DIR="$HA_CONFIG/.storage" HA_DOMAIN="$domain" python3 - <<'PY'
import json
import os
import pathlib
import sys

storage = pathlib.Path(os.environ["HA_STORAGE_DIR"]) / "core.config_entries"
if not storage.exists():
    sys.exit(1)

try:
    data = json.loads(storage.read_text())
except Exception:
    sys.exit(1)

entries = data.get("data", {}).get("entries", [])
found = any(entry.get("domain") == os.environ["HA_DOMAIN"] for entry in entries)
sys.exit(0 if found else 1)
PY
}

wait_for_storage_domain() {
  local domain="$1"
  local timeout_seconds="${2:-30}"

  for i in $(seq 1 "$timeout_seconds"); do
    if ha_storage_has_domain "$domain"; then
      echo "    Persisted $domain config entry after ${i}s"
      return 0
    fi
    sleep 1
  done

  return 1
}

echo "==> Stopping any existing test containers..."
docker rm -f ha-test-mosquitto 2>/dev/null || true
docker rm -f ha-test-homeassistant 2>/dev/null || true

echo "==> Wiping HA config for fresh start..."
sudo rm -rf "$HA_CONFIG"
mkdir -p "$HA_CONFIG"

# ── Mosquitto ─────────────────────────────────────────────────────────────

echo "==> Starting Mosquitto (port 1883, no auth)..."
MOSQUITTO_CONF=$(mktemp)
cat > "$MOSQUITTO_CONF" << 'MQTTEOF'
listener 1883 0.0.0.0
allow_anonymous true
MQTTEOF

docker run -d \
  --name ha-test-mosquitto \
  --network host \
  -v "$MOSQUITTO_CONF:/mosquitto/config/mosquitto.conf:ro" \
  eclipse-mosquitto:2

# Give Mosquitto a moment to bind
sleep 2
rm -f "$MOSQUITTO_CONF"

# ── Home Assistant ────────────────────────────────────────────────────────

echo "==> Starting Home Assistant (port 8123)..."
docker run -d \
  --name ha-test-homeassistant \
  --network host \
  -v "$HA_CONFIG:/config" \
  ghcr.io/home-assistant/home-assistant:stable

echo "==> Waiting for HA to boot..."
for i in $(seq 1 90); do
  HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8123/api/onboarding/users 2>/dev/null || echo "000")
  if echo "$HTTP_CODE" | grep -q '200\|405'; then
    echo "    HA is up after ${i}s"
    break
  fi
  if [ "$i" -eq 90 ]; then
    echo "    ERROR: HA did not start within 90s"
    echo "    Check: docker logs ha-test-homeassistant"
    exit 1
  fi
  sleep 1
done

# ── Onboarding ────────────────────────────────────────────────────────────

echo "==> Running onboarding (user: dev / pass: dev)..."
ONBOARD_RESP=$(curl -s -X POST http://localhost:8123/api/onboarding/users \
  -H "Content-Type: application/json" \
  -d "{\"client_id\":\"$HA_CLIENT_ID\",\"name\":\"Dev\",\"username\":\"dev\",\"password\":\"dev\",\"language\":\"en\"}")

AUTH_CODE=$(echo "$ONBOARD_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('auth_code',''))" 2>/dev/null || echo "")
if [ -z "$AUTH_CODE" ]; then
  echo "    WARNING: Could not extract auth_code from onboarding. HA may already be onboarded."
  echo "    Response: $ONBOARD_RESP"
  echo ""
  echo "    Skipping MQTT auto-config. Configure MQTT manually:"
  echo "    Settings > Devices & Services > Add Integration > MQTT"
  echo "    Broker: 127.0.0.1  Port: 1883"
  echo ""
  echo "==> Done! Open http://localhost:8123"
  exit 0
fi

# Exchange auth code for tokens
echo "==> Exchanging auth code for access token..."
TOKEN_RESP=$(curl -s -X POST http://localhost:8123/auth/token \
  -d "grant_type=authorization_code&code=$AUTH_CODE&client_id=$HA_CLIENT_ID")

ACCESS_TOKEN=$(echo "$TOKEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || echo "")
if [ -z "$ACCESS_TOKEN" ]; then
  echo "    WARNING: Could not get access token."
  echo "    Configure MQTT manually: Settings > Devices & Services > Add Integration > MQTT"
  echo "    Broker: 127.0.0.1  Port: 1883"
  echo ""
  echo "==> Done! Open http://localhost:8123 and log in as dev/dev"
  exit 0
fi

# Complete remaining onboarding steps
echo "==> Completing onboarding steps..."
curl -s -X POST http://localhost:8123/api/onboarding/core_config \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' > /dev/null 2>&1 || true

curl -s -X POST http://localhost:8123/api/onboarding/analytics \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' > /dev/null 2>&1 || true

curl -s -X POST http://localhost:8123/api/onboarding/integration \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"client_id\":\"$HA_CLIENT_ID\"}" > /dev/null 2>&1 || true

# ── Configure MQTT integration ───────────────────────────────────────────

echo "==> Adding MQTT integration (broker: 127.0.0.1:1883)..."
FLOW_RESP=$(curl -s -X POST http://localhost:8123/api/config/config_entries/flow \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"handler":"mqtt"}')

FLOW_ID=$(echo "$FLOW_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('flow_id',''))" 2>/dev/null || echo "")
if [ -z "$FLOW_ID" ]; then
  echo "    WARNING: Could not start MQTT config flow."
  echo "    Response: $FLOW_RESP"
  echo "    Configure MQTT manually: Settings > Devices & Services > Add Integration > MQTT"
  echo "    Broker: 127.0.0.1  Port: 1883"
else
  MQTT_RESULT=$(curl -s -X POST "http://localhost:8123/api/config/config_entries/flow/$FLOW_ID" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"broker":"127.0.0.1","port":1883,"username":"","password":""}')

  RESULT_TYPE=$(echo "$MQTT_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('type',''))" 2>/dev/null || echo "")
  if [ "$RESULT_TYPE" = "create_entry" ]; then
    echo "    MQTT integration configured in HA; waiting for storage flush..."
    if wait_for_storage_domain "mqtt" 30; then
      echo "    MQTT integration configured successfully."
    else
      echo "    ERROR: MQTT config entry never persisted to $HA_CONFIG/.storage/core.config_entries"
      echo "    Response: $MQTT_RESULT"
      exit 1
    fi
  else
    echo "    WARNING: MQTT config flow returned: $RESULT_TYPE"
    echo "    Response: $MQTT_RESULT"
    echo "    You may need to configure MQTT manually."
  fi
fi

# ── Debug logging ─────────────────────────────────────────────────────────

echo "==> Enabling MQTT debug logging..."
sudo tee -a "$HA_CONFIG/configuration.yaml" > /dev/null << 'EOF'

logger:
  default: warning
  logs:
    homeassistant.components.mqtt: info
EOF

# Gracefully stop the backgrounded HA so it flushes config to disk
# (docker rm -f sends SIGKILL which loses in-memory state like the MQTT config entry)
echo "==> Stopping background HA (graceful, flushing config)..."
docker stop ha-test-homeassistant > /dev/null 2>&1
docker rm ha-test-homeassistant > /dev/null 2>&1

if ! ha_storage_has_domain "mqtt"; then
  echo "    ERROR: MQTT config entry disappeared after Home Assistant shutdown."
  echo "    Check $HA_CONFIG/.storage/core.config_entries"
  exit 1
fi

# ── Summary ───────────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "  HA test environment ready!"
echo "============================================================"
echo ""
echo "  Home Assistant:  http://localhost:8123  (dev / dev)"
echo "  Mosquitto:       localhost:1883 (no auth)"
echo "  MQTT integration: pre-configured"
echo ""
echo "  Next steps:"
echo "    1. Start RemoteTerm as usual"
echo "    2. In RemoteTerm: Settings > Integrations > Add > Home Assistant"
echo "    3. Set Broker Host: 127.0.0.1, Port: 1883"
echo "    4. Select contacts for GPS tracking and/or repeaters for telemetry"
echo "    5. Save and enable"
echo "    6. In HA: Settings > Devices & Services > MQTT"
echo "       You should see MeshCore devices appearing automatically"
echo ""
echo "  MQTT debug tool (in another terminal):"
echo "    docker exec ha-test-mosquitto mosquitto_sub -h 127.0.0.1 -t '#' -v"
echo ""
echo "  Tear down: Ctrl+C here, then ./scripts/setup/stop_ha_test_env.sh"
echo ""
echo "==> Starting Home Assistant in foreground (Ctrl+C to stop)..."
echo ""

exec docker run --rm \
  --name ha-test-homeassistant \
  --network host \
  -v "$HA_CONFIG:/config" \
  ghcr.io/home-assistant/home-assistant:stable
