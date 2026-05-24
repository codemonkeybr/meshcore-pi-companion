#!/usr/bin/env bash
# install_service.sh
#
# Sets up RemoteTerm for MeshCore as a persistent systemd service running as
# the current user from the current repo directory. No separate service account
# is needed. After installation, git pull and rebuilds work without any sudo -u
# gymnastics.
#
# Run from anywhere inside the repo:
#   bash scripts/setup/install_service.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SERVICE_NAME="remoteterm"
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CURRENT_USER="$(id -un)"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
FRONTEND_MODE="build"

echo -e "${BOLD}=== RemoteTerm for MeshCore — Service Installer ===${NC}"
echo

# ── sanity checks ──────────────────────────────────────────────────────────────

if [ "$(uname -s)" != "Linux" ]; then
    echo -e "${RED}Error: this script is for Linux (systemd) only.${NC}"
    exit 1
fi

if ! command -v systemctl &>/dev/null; then
    echo -e "${RED}Error: systemd not found. This script requires a systemd-based Linux system.${NC}"
    exit 1
fi

if ! command -v uv &>/dev/null; then
    echo -e "${RED}Error: 'uv' not found. Install it first:${NC}"
    echo    "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo -e "${RED}Error: python3 is required but was not found.${NC}"
    exit 1
fi

UV_BIN="$(command -v uv)"
UVICORN_BIN="$REPO_DIR/.venv/bin/uvicorn"

echo -e "  Installing as user : ${CYAN}${CURRENT_USER}${NC}"
echo -e "  Repo directory     : ${CYAN}${REPO_DIR}${NC}"
echo -e "  Service name       : ${CYAN}${SERVICE_NAME}${NC}"
echo -e "  uv                 : ${CYAN}${UV_BIN}${NC}"
echo

version_major() {
    local version="$1"
    version="${version#v}"
    printf '%s' "${version%%.*}"
}

require_minimum_version() {
    local tool_name="$1"
    local detected_version="$2"
    local minimum_major="$3"
    local major
    major="$(version_major "$detected_version")"
    if ! [[ "$major" =~ ^[0-9]+$ ]] || [ "$major" -lt "$minimum_major" ]; then
        echo -e "${RED}Error: ${tool_name} ${minimum_major}+ is required for a local frontend build, but found ${detected_version}.${NC}"
        exit 1
    fi
}

# ── transport selection ────────────────────────────────────────────────────────

echo -e "${BOLD}─── Transport ───────────────────────────────────────────────────────${NC}"
echo "How is your MeshCore radio connected?"
echo "  1) Serial — auto-detect port (default)"
echo "  2) Serial — specify port manually"
echo "  3) TCP (network connection)"
echo "  4) BLE (Bluetooth)"
echo
read -rp "Select transport [1-4] (default: 1): " TRANSPORT_CHOICE
TRANSPORT_CHOICE="${TRANSPORT_CHOICE:-1}"
echo

NEED_DIALOUT=false
SERIAL_PORT=""
TCP_HOST=""
TCP_PORT=""
BLE_ADDRESS=""
BLE_PIN=""

case "$TRANSPORT_CHOICE" in
    1)
        echo -e "${GREEN}Serial auto-detect selected.${NC}"
        NEED_DIALOUT=true
        ;;
    2)
        read -rp "Serial port path (default: /dev/ttyUSB0): " SERIAL_PORT
        SERIAL_PORT="${SERIAL_PORT:-/dev/ttyUSB0}"
        echo -e "${GREEN}Serial port: ${SERIAL_PORT}${NC}"
        NEED_DIALOUT=true
        ;;
    3)
        read -rp "TCP host (IP address or hostname): " TCP_HOST
        while [ -z "$TCP_HOST" ]; do
            echo -e "${RED}TCP host is required.${NC}"
            read -rp "TCP host: " TCP_HOST
        done
        read -rp "TCP port (default: 5000): " TCP_PORT
        TCP_PORT="${TCP_PORT:-5000}"
        echo -e "${GREEN}TCP: ${TCP_HOST}:${TCP_PORT}${NC}"
        ;;
    4)
        read -rp "BLE device address (e.g. AA:BB:CC:DD:EE:FF): " BLE_ADDRESS
        while [ -z "$BLE_ADDRESS" ]; do
            echo -e "${RED}BLE address is required.${NC}"
            read -rp "BLE device address: " BLE_ADDRESS
        done
        read -rsp "BLE PIN: " BLE_PIN
        echo
        while [ -z "$BLE_PIN" ]; do
            echo -e "${RED}BLE PIN is required.${NC}"
            read -rsp "BLE PIN: " BLE_PIN
            echo
        done
        echo -e "${GREEN}BLE: ${BLE_ADDRESS}${NC}"
        ;;
    *)
        echo -e "${YELLOW}Invalid selection — defaulting to serial auto-detect.${NC}"
        TRANSPORT_CHOICE=1
        NEED_DIALOUT=true
        ;;
esac
echo

# ── frontend install mode ──────────────────────────────────────────────────────

echo -e "${BOLD}─── Frontend Assets ─────────────────────────────────────────────────${NC}"
echo "How should the frontend be installed?"
echo "  1) Build locally with npm (default, latest code, requires node/npm)"
echo "  2) Download prebuilt frontend (fastest)"
echo
read -rp "Select frontend mode [1-2] (default: 1): " FRONTEND_CHOICE
FRONTEND_CHOICE="${FRONTEND_CHOICE:-1}"
echo

case "$FRONTEND_CHOICE" in
    1)
        FRONTEND_MODE="build"
        echo -e "${GREEN}Using local frontend build.${NC}"
        ;;
    2)
        FRONTEND_MODE="prebuilt"
        echo -e "${GREEN}Using prebuilt frontend download.${NC}"
        ;;
    *)
        FRONTEND_MODE="build"
        echo -e "${YELLOW}Invalid selection — defaulting to local frontend build.${NC}"
        ;;
esac
echo

# ── bots ──────────────────────────────────────────────────────────────────────

echo -e "${BOLD}─── Bot System ──────────────────────────────────────────────────────${NC}"
echo -e "${YELLOW}Warning:${NC} The bot system executes arbitrary Python code on the server."
echo    "It is not recommended on untrusted networks. You can always enable"
echo    "it later by editing the service file."
echo
read -rp "Enable bots? [y/N]: " ENABLE_BOTS
ENABLE_BOTS="${ENABLE_BOTS:-N}"
echo

ENABLE_AUTH="N"
AUTH_USERNAME=""
AUTH_PASSWORD=""

if [[ "$ENABLE_BOTS" =~ ^[Yy] ]]; then
    echo -e "${GREEN}Bots enabled.${NC}"
    echo

    echo -e "${BOLD}─── HTTP Basic Auth ─────────────────────────────────────────────────${NC}"
    echo "With bots enabled, HTTP Basic Auth is strongly recommended if this"
    echo "service will be accessible beyond your local machine."
    echo
    read -rp "Set up HTTP Basic Auth? [Y/n]: " ENABLE_AUTH
    ENABLE_AUTH="${ENABLE_AUTH:-Y}"
    echo

    if [[ "$ENABLE_AUTH" =~ ^[Yy] ]]; then
        read -rp "Username: " AUTH_USERNAME
        while [ -z "$AUTH_USERNAME" ]; do
            echo -e "${RED}Username cannot be empty.${NC}"
            read -rp "Username: " AUTH_USERNAME
        done
        read -rsp "Password: " AUTH_PASSWORD
        echo
        while [ -z "$AUTH_PASSWORD" ]; do
            echo -e "${RED}Password cannot be empty.${NC}"
            read -rsp "Password: " AUTH_PASSWORD
            echo
        done
        echo -e "${GREEN}Basic Auth configured for user '${AUTH_USERNAME}'.${NC}"
        echo -e "${YELLOW}Note:${NC} Basic Auth credentials are not safe over plain HTTP."
        echo    "See README_ADVANCED.md for HTTPS setup."
    fi
else
    echo -e "${GREEN}Bots disabled.${NC}"
fi
echo

# ── python dependencies ────────────────────────────────────────────────────────

echo -e "${YELLOW}Installing Python dependencies (uv sync)...${NC}"
cd "$REPO_DIR"
uv sync
echo -e "${GREEN}Dependencies ready.${NC}"
echo

# ── frontend assets ────────────────────────────────────────────────────────────

if [ "$FRONTEND_MODE" = "build" ]; then
    if ! command -v node &>/dev/null; then
        echo -e "${RED}Error: node is required for a local frontend build but was not found.${NC}"
        echo -e "${YELLOW}Tip:${NC} Re-run the installer and choose the prebuilt frontend option, or install Node.js 18+ and npm 9+."
        exit 1
    fi
    if ! command -v npm &>/dev/null; then
        echo -e "${RED}Error: npm is required for a local frontend build but was not found.${NC}"
        echo -e "${YELLOW}Tip:${NC} Re-run the installer and choose the prebuilt frontend option, or install Node.js 18+ and npm 9+."
        exit 1
    fi

    NODE_VERSION="$(node -v)"
    NPM_VERSION="$(npm -v)"
    require_minimum_version "Node.js" "$NODE_VERSION" 20
    require_minimum_version "npm" "$NPM_VERSION" 9

    echo -e "${YELLOW}Building frontend locally with Node ${NODE_VERSION} and npm ${NPM_VERSION}...${NC}"
    (
        cd "$REPO_DIR/frontend"
        npm install
        npm run build
    )
else
    echo -e "${YELLOW}Fetching prebuilt frontend...${NC}"
    python3 "$REPO_DIR/scripts/setup/fetch_prebuilt_frontend.py"
fi
echo

# ── data directory ─────────────────────────────────────────────────────────────

mkdir -p "$REPO_DIR/data"

# ── serial port access ─────────────────────────────────────────────────────────

if [ "$NEED_DIALOUT" = true ]; then
    if ! id -nG "$CURRENT_USER" | grep -qw dialout; then
        echo -e "${YELLOW}Adding ${CURRENT_USER} to the 'dialout' group for serial port access...${NC}"
        sudo usermod -aG dialout "$CURRENT_USER"
        echo -e "${GREEN}Done. You may need to log out and back in for this to take effect for${NC}"
        echo -e "${GREEN}manual runs; the service itself handles it via SupplementaryGroups.${NC}"
        echo
    else
        echo -e "${GREEN}User ${CURRENT_USER} is already in the 'dialout' group.${NC}"
        echo
    fi
fi

# ── systemd service file ───────────────────────────────────────────────────────

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e "${YELLOW}${SERVICE_NAME} is currently running; stopping it before applying changes...${NC}"
    sudo systemctl stop "$SERVICE_NAME"
    echo
fi

echo -e "${YELLOW}Writing systemd service file to ${SERVICE_FILE}...${NC}"

# Escape a value for use in a systemd Environment= directive.
# Must handle: % (specifier expansion), " and \ (systemd.syntax unquoting),
# and trailing backslash (line continuation). Wraps in double quotes so
# spaces are preserved.
systemd_escape_env_value() {
    local v="$1"
    v="${v//\\/\\\\}"   # \ → \\  (must be first)
    v="${v//\"/\\\"}"   # " → \"
    v="${v//%/%%}"      # % → %%
    printf '"%s"' "$v"
}

generate_service_file() {
    echo "[Unit]"
    echo "Description=RemoteTerm for MeshCore"
    echo "After=network.target"
    echo ""
    echo "[Service]"
    echo "Type=simple"
    echo "User=${CURRENT_USER}"
    echo "WorkingDirectory=${REPO_DIR}"
    echo "ExecStart=${UVICORN_BIN} app.main:app --host 0.0.0.0 --port 8000"
    echo "Restart=always"
    echo "RestartSec=5"
    echo "Environment=MESHCORE_DATABASE_PATH=${REPO_DIR}/data/meshcore.db"

    # Transport
    case "$TRANSPORT_CHOICE" in
        2) echo "Environment=MESHCORE_SERIAL_PORT=$(systemd_escape_env_value "$SERIAL_PORT")" ;;
        3)
            echo "Environment=MESHCORE_TCP_HOST=$(systemd_escape_env_value "$TCP_HOST")"
            echo "Environment=MESHCORE_TCP_PORT=$(systemd_escape_env_value "$TCP_PORT")"
            ;;
        4)
            echo "Environment=MESHCORE_BLE_ADDRESS=$(systemd_escape_env_value "$BLE_ADDRESS")"
            echo "Environment=MESHCORE_BLE_PIN=$(systemd_escape_env_value "$BLE_PIN")"
            ;;
    esac

    # Bots
    if [[ ! "$ENABLE_BOTS" =~ ^[Yy] ]]; then
        echo "Environment=MESHCORE_DISABLE_BOTS=true"
    fi

    # Basic auth
    if [[ "$ENABLE_BOTS" =~ ^[Yy] ]] && [[ "$ENABLE_AUTH" =~ ^[Yy] ]]; then
        echo "Environment=MESHCORE_BASIC_AUTH_USERNAME=$(systemd_escape_env_value "$AUTH_USERNAME")"
        echo "Environment=MESHCORE_BASIC_AUTH_PASSWORD=$(systemd_escape_env_value "$AUTH_PASSWORD")"
    fi

    # Serial group access
    if [ "$NEED_DIALOUT" = true ]; then
        echo "SupplementaryGroups=dialout"
    fi

    echo ""
    echo "[Install]"
    echo "WantedBy=multi-user.target"
}

generate_service_file | sudo tee "$SERVICE_FILE" > /dev/null

echo -e "${GREEN}Service file written.${NC}"
echo

# ── enable and start ───────────────────────────────────────────────────────────

echo -e "${YELLOW}Reloading systemd and applying ${SERVICE_NAME}...${NC}"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl start "$SERVICE_NAME"
echo

# ── status check ───────────────────────────────────────────────────────────────

echo -e "${YELLOW}Service status:${NC}"
sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
echo

# ── summary ────────────────────────────────────────────────────────────────────

echo -e "${GREEN}${BOLD}=== Installation complete! ===${NC}"
echo
echo -e "RemoteTerm is running at ${CYAN}http://$(hostname -I | awk '{print $1}'):8000${NC}"
echo

case "$TRANSPORT_CHOICE" in
    1) echo -e "  Transport : ${CYAN}Serial (auto-detect)${NC}" ;;
    2) echo -e "  Transport : ${CYAN}Serial (${SERIAL_PORT})${NC}" ;;
    3) echo -e "  Transport : ${CYAN}TCP (${TCP_HOST}:${TCP_PORT})${NC}" ;;
    4) echo -e "  Transport : ${CYAN}BLE (${BLE_ADDRESS})${NC}" ;;
esac
if [ "$FRONTEND_MODE" = "build" ]; then
    echo -e "  Frontend  : ${GREEN}Built locally${NC}"
else
    echo -e "  Frontend  : ${YELLOW}Prebuilt download${NC}"
fi

if [[ "$ENABLE_BOTS" =~ ^[Yy] ]]; then
    echo -e "  Bots      : ${YELLOW}Enabled${NC}"
    if [[ "$ENABLE_AUTH" =~ ^[Yy] ]]; then
        echo -e "  Basic Auth: ${GREEN}Enabled (user: ${AUTH_USERNAME})${NC}"
    else
        echo -e "  Basic Auth: ${YELLOW}Not configured${NC}"
    fi
else
    echo -e "  Bots      : ${GREEN}Disabled${NC} (edit ${SERVICE_FILE} to enable)"
fi
echo

if [ "$FRONTEND_MODE" = "prebuilt" ]; then
    echo -e "${YELLOW}Note:${NC} A prebuilt frontend has been fetched and installed. It may lag"
    echo    "behind the latest code. To build the frontend from source for the most"
    echo    "up-to-date features later, run:"
    echo
    echo -e "  ${CYAN}cd ${REPO_DIR}/frontend && npm install && npm run build${NC}"
    echo
fi

echo -e "${BOLD}─── Quick Reference ─────────────────────────────────────────────────${NC}"
echo
echo -e "${YELLOW}Update to latest and restart:${NC}"
echo -e "  cd ${REPO_DIR}"
echo -e "  git pull"
echo -e "  uv sync"
echo -e "  cd frontend && npm install && npm run build && cd .."
echo -e "  sudo systemctl restart ${SERVICE_NAME}"
echo
echo -e "${YELLOW}Refresh prebuilt frontend only (skips local build):${NC}"
echo -e "  python3 ${REPO_DIR}/scripts/setup/fetch_prebuilt_frontend.py"
echo -e "  sudo systemctl restart ${SERVICE_NAME}"
echo
echo -e "${YELLOW}View live logs (useful for troubleshooting):${NC}"
echo -e "  sudo journalctl -u ${SERVICE_NAME} -f"
echo
echo -e "${YELLOW}Service control:${NC}"
echo -e "  sudo systemctl start|stop|restart|status ${SERVICE_NAME}"
echo -e "${BOLD}─────────────────────────────────────────────────────────────────────${NC}"
