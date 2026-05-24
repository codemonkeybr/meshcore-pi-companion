#!/usr/bin/env bash
# install_docker.sh
#
# Generates a local docker-compose.yml for RemoteTerm from a guided prompt flow.
# The generated compose file is intentionally gitignored so local customization
# does not create merge churn on future pulls.
#
# Run from anywhere inside the repo:
#   bash scripts/setup/install_docker.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
PURPLE='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_FILE="$REPO_DIR/docker-compose.yml"
EXAMPLE_FILE="$REPO_DIR/docker-compose.example.yml"
SNAKEOIL_CERT_DIR="$REPO_DIR/.docker-certs"
SNAKEOIL_CERT_BASENAME="remoteterm-snakeoil.crt"
SNAKEOIL_KEY_BASENAME="remoteterm-snakeoil.key"
SNAKEOIL_CERT_HOST_PATH="$SNAKEOIL_CERT_DIR/$SNAKEOIL_CERT_BASENAME"
SNAKEOIL_KEY_HOST_PATH="$SNAKEOIL_CERT_DIR/$SNAKEOIL_KEY_BASENAME"
SNAKEOIL_CERT_CONTAINER_PATH="/app/certs/$SNAKEOIL_CERT_BASENAME"
SNAKEOIL_KEY_CONTAINER_PATH="/app/certs/$SNAKEOIL_KEY_BASENAME"

IMAGE_MODE="image"
TRANSPORT_MODE="serial"
SERIAL_HOST_PATH="/dev/ttyACM0"
SERIAL_COMPOSE_HOST_PATH="/dev/ttyACM0"
SERIAL_CONTAINER_PATH="/dev/meshcore-radio"
TCP_HOST=""
TCP_PORT="5000"
BLE_ADDRESS=""
BLE_PIN=""
ENABLE_BOTS="N"
ENABLE_AUTH="N"
AUTH_USERNAME=""
AUTH_PASSWORD=""
RUN_AS_HOST_USER="N"
ENABLE_SNAKEOIL_TLS="Y"
BLE_MANUAL_WARNING=false
LOCAL_ACCESS_IP=""
SERIAL_FOUND_HOST_PATHS=()
SERIAL_FOUND_LABELS=()
SERIAL_FOUND_DISPLAYS=()

find_serial_devices() {
    local path
    local resolved
    local label
    local existing

    SERIAL_FOUND_HOST_PATHS=()
    SERIAL_FOUND_LABELS=()
    SERIAL_FOUND_DISPLAYS=()

    if [ -d /dev/serial/by-id ]; then
        while IFS= read -r path; do
            [ -n "$path" ] || continue
            resolved="$(readlink -f "$path" 2>/dev/null || true)"
            [ -n "$resolved" ] || resolved="$path"
            label="$(basename "$path")"
            SERIAL_FOUND_HOST_PATHS+=("$path")
            SERIAL_FOUND_LABELS+=("$label")
            SERIAL_FOUND_DISPLAYS+=("$path -> $resolved")
        done < <(find /dev/serial/by-id -maxdepth 1 -type l | sort)
    fi

    for path in /dev/ttyACM* /dev/ttyUSB* /dev/cu.usbmodem* /dev/cu.usbserial*; do
        [ -e "$path" ] || continue
        resolved="$(readlink -f "$path" 2>/dev/null || true)"
        [ -n "$resolved" ] || resolved="$path"

        if ((${#SERIAL_FOUND_HOST_PATHS[@]} > 0)); then
            for existing in "${SERIAL_FOUND_DISPLAYS[@]}"; do
                if [[ "$existing" = *"-> $resolved" ]]; then
                    resolved=""
                    break
                fi
            done
            [ -n "$resolved" ] || continue
        fi

        SERIAL_FOUND_HOST_PATHS+=("$path")
        SERIAL_FOUND_LABELS+=("$(basename "$path")")
        SERIAL_FOUND_DISPLAYS+=("$path")
    done
}

yaml_quote() {
    local value="$1"
    value=${value//\'/\'\'}
    printf "'%s'" "$value"
}

normalize_serial_host_path_for_compose() {
    local selected_path="$1"
    local resolved_path=""

    if [[ "$selected_path" != *:* ]]; then
        SERIAL_COMPOSE_HOST_PATH="$selected_path"
        return 0
    fi

    resolved_path="$(readlink -f "$selected_path" 2>/dev/null || true)"
    if [ -z "$resolved_path" ]; then
        echo -e "${RED}Error:${NC} the selected serial path contains ':' and could not be resolved to a raw /dev/tty-style device path."
        echo "Selected path: $selected_path"
        echo "Please enter the raw serial device path instead (for example /dev/ttyACM0)."
        exit 1
    fi

    if [[ "$resolved_path" == *:* ]]; then
        echo -e "${RED}Error:${NC} the selected serial path still resolves to a path containing ':', which Docker Compose cannot use here."
        echo "Selected path: $selected_path"
        echo "Resolved path: $resolved_path"
        echo "Please enter the raw serial device path instead (for example /dev/ttyACM0)."
        exit 1
    fi

    echo -e "${YELLOW}Note:${NC} the selected serial path contains ':', so Docker Compose will use the resolved raw device path instead: ${resolved_path}"
    SERIAL_COMPOSE_HOST_PATH="$resolved_path"
}

detect_primary_local_ip() {
    local ip=""
    local iface=""

    if command -v hostname &>/dev/null; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi

    if [ -z "$ip" ] && command -v ip &>/dev/null; then
        ip="$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')"
    fi

    if [ -z "$ip" ] && command -v route &>/dev/null && command -v ipconfig &>/dev/null; then
        iface="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
        if [ -n "$iface" ]; then
            ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
        fi
    fi

    if [ -z "$ip" ]; then
        ip="127.0.0.1"
    fi

    printf '%s' "$ip"
}

ensure_snakeoil_requirements() {
    local dep

    for dep in openssl mktemp; do
        if ! command -v "$dep" &>/dev/null; then
            echo -e "${RED}Error: ${dep} is required to generate the snakeoil TLS certificate.${NC}"
            exit 1
        fi
    done
}

generate_snakeoil_certificate() {
    local san_ip="$1"
    local tmp_config=""

    mkdir -p "$SNAKEOIL_CERT_DIR"
    tmp_config="$(mktemp)"

    cat >"$tmp_config" <<EOF
[req]
default_bits = 2048
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = RemoteTerm Snakeoil
O = RemoteTerm for MeshCore

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
IP.1 = 127.0.0.1
EOF

    if [ -n "$san_ip" ] && [ "$san_ip" != "127.0.0.1" ]; then
        printf 'IP.2 = %s\n' "$san_ip" >>"$tmp_config"
    fi

    openssl req \
        -x509 \
        -nodes \
        -newkey rsa:2048 \
        -days 3650 \
        -keyout "$SNAKEOIL_KEY_HOST_PATH" \
        -out "$SNAKEOIL_CERT_HOST_PATH" \
        -config "$tmp_config" \
        -extensions v3_req >/dev/null 2>&1

    rm -f "$tmp_config"

    chmod 600 "$SNAKEOIL_KEY_HOST_PATH"
    chmod 644 "$SNAKEOIL_CERT_HOST_PATH"
}

if ! command -v docker &>/dev/null; then
    echo -e "${RED}Warning: docker was not found in PATH. A compose file will still be generated, but you will need Docker installed to run it.${NC}"
elif ! docker compose version &>/dev/null; then
    echo -e "${RED}Warning: docker compose is not available. A compose file will still be generated, but you will need the Docker Compose plugin to run it.${NC}"
fi

echo -e "${BOLD}=== RemoteTerm for MeshCore — Docker Setup ===${NC}"
echo
echo -e "  Repo directory     : ${CYAN}${REPO_DIR}${NC}"
echo -e "  Example compose    : ${CYAN}${EXAMPLE_FILE}${NC}"
echo -e "  Output compose     : ${CYAN}${COMPOSE_FILE}${NC}"
echo

if [ -f "$COMPOSE_FILE" ]; then
    echo -e "${YELLOW}A local docker-compose.yml already exists.${NC}"
    read -r -p "Overwrite it? [y/N]: " OVERWRITE
    OVERWRITE="${OVERWRITE:-N}"
    if ! [[ "$OVERWRITE" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Leaving the existing compose file untouched.${NC}"
        exit 0
    fi
fi

echo -e "${BOLD}─── Image Source ────────────────────────────────────────────────────${NC}"
echo "How should Docker run RemoteTerm?"
echo "  1) Use the published Docker Hub image (default)"
echo "  2) Build locally from this checkout"
echo
read -r -p "Select image mode [1-2] (default: 1): " IMAGE_CHOICE
IMAGE_CHOICE="${IMAGE_CHOICE:-1}"
echo

case "$IMAGE_CHOICE" in
    1)
        IMAGE_MODE="image"
        echo -e "${GREEN}Using published Docker image.${NC}"
        ;;
    2)
        IMAGE_MODE="build"
        echo -e "${GREEN}Using local Docker build.${NC}"
        ;;
    *)
        IMAGE_MODE="image"
        echo -e "${YELLOW}Invalid selection; defaulting to published Docker image.${NC}"
        ;;
esac
echo

echo -e "${BOLD}─── Transport ───────────────────────────────────────────────────────${NC}"
echo "How will the container reach your MeshCore radio?"
echo "  1) Serial device passthrough (default)"
echo "  2) TCP"
echo "  3) BLE"
echo
echo "BLE can be configured here, but Docker Bluetooth access still requires manual compose customization."
echo
read -r -p "Select transport [1-3] (default: 1): " TRANSPORT_CHOICE
TRANSPORT_CHOICE="${TRANSPORT_CHOICE:-1}"
echo

case "$TRANSPORT_CHOICE" in
    1)
        TRANSPORT_MODE="serial"
        find_serial_devices

        if ((${#SERIAL_FOUND_HOST_PATHS[@]} == 0)); then
            echo -e "${YELLOW}No serial devices were auto-detected.${NC}"
            read -r -p "Serial device path on the host (default: /dev/ttyACM0): " SERIAL_HOST_PATH
            SERIAL_HOST_PATH="${SERIAL_HOST_PATH:-/dev/ttyACM0}"
        else
            echo "Detected serial devices:"
            for i in "${!SERIAL_FOUND_HOST_PATHS[@]}"; do
                printf '  %d) %s (%s)\n' "$((i + 1))" "${SERIAL_FOUND_LABELS[$i]}" "${SERIAL_FOUND_DISPLAYS[$i]}"
            done
            echo "  m) Enter a path manually"
            echo
            read -r -p "Select serial device [1-${#SERIAL_FOUND_HOST_PATHS[@]} or m] (default: 1): " SERIAL_CHOICE
            SERIAL_CHOICE="${SERIAL_CHOICE:-1}"

            if [[ "$SERIAL_CHOICE" =~ ^[Mm]$ ]]; then
                read -r -p "Serial device path on the host (default: ${SERIAL_FOUND_HOST_PATHS[0]}): " SERIAL_HOST_PATH
                SERIAL_HOST_PATH="${SERIAL_HOST_PATH:-${SERIAL_FOUND_HOST_PATHS[0]}}"
            elif [[ "$SERIAL_CHOICE" =~ ^[0-9]+$ ]] && [ "$SERIAL_CHOICE" -ge 1 ] && [ "$SERIAL_CHOICE" -le "${#SERIAL_FOUND_HOST_PATHS[@]}" ]; then
                SERIAL_HOST_PATH="${SERIAL_FOUND_HOST_PATHS[$((SERIAL_CHOICE - 1))]}"
            else
                SERIAL_HOST_PATH="${SERIAL_FOUND_HOST_PATHS[0]}"
                echo -e "${YELLOW}Invalid selection; defaulting to ${SERIAL_HOST_PATH}.${NC}"
            fi
        fi

        normalize_serial_host_path_for_compose "$SERIAL_HOST_PATH"
        echo -e "${GREEN}Serial passthrough: ${SERIAL_COMPOSE_HOST_PATH} -> ${SERIAL_CONTAINER_PATH}${NC}"
        ;;
    2)
        TRANSPORT_MODE="tcp"
        read -r -p "TCP host (IP address or hostname): " TCP_HOST
        while [ -z "$TCP_HOST" ]; do
            echo -e "${RED}TCP host is required.${NC}"
            read -r -p "TCP host: " TCP_HOST
        done
        read -r -p "TCP port (default: 5000): " TCP_PORT
        TCP_PORT="${TCP_PORT:-5000}"
        echo -e "${GREEN}TCP: ${TCP_HOST}:${TCP_PORT}${NC}"
        ;;
    3)
        TRANSPORT_MODE="ble"
        read -r -p "BLE device address (e.g. AA:BB:CC:DD:EE:FF): " BLE_ADDRESS
        while [ -z "$BLE_ADDRESS" ]; do
            echo -e "${RED}BLE address is required.${NC}"
            read -r -p "BLE device address: " BLE_ADDRESS
        done
        read -r -s -p "BLE PIN: " BLE_PIN
        echo
        while [ -z "$BLE_PIN" ]; do
            echo -e "${RED}BLE PIN is required.${NC}"
            read -r -s -p "BLE PIN: " BLE_PIN
            echo
        done
        echo -e "${GREEN}BLE: ${BLE_ADDRESS}${NC}"
        echo
        echo -e "${RED}BLE Docker warning:${NC} Bluetooth access is not fully automated here."
        echo -e "${RED}You will need to customize docker-compose.yml manually before BLE works.${NC}"
        echo "That may include passing through Bluetooth devices, enabling privileged mode,"
        echo "using host networking, or other host-specific Docker changes."
        echo "If you want the easier path, use the regular Python launch flow for BLE instead."
        BLE_MANUAL_WARNING=true
        ;;
    *)
        TRANSPORT_MODE="serial"
        SERIAL_HOST_PATH="/dev/ttyACM0"
        echo -e "${YELLOW}Invalid selection; defaulting to serial passthrough at ${SERIAL_HOST_PATH}.${NC}"
        ;;
esac
echo

echo -e "${BOLD}─── Bot System ──────────────────────────────────────────────────────${NC}"
echo -e "${YELLOW}Warning:${NC} The bot system executes arbitrary Python code on the server."
echo "It is not recommended on untrusted networks."
echo
read -r -p "Enable bots? [y/N]: " ENABLE_BOTS
ENABLE_BOTS="${ENABLE_BOTS:-N}"
echo

if [[ "$ENABLE_BOTS" =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}Bots enabled.${NC}"
else
    echo -e "${GREEN}Bots disabled.${NC}"
fi
echo

echo -e "${BOLD}─── HTTP Basic Auth ─────────────────────────────────────────────────${NC}"
if [[ "$ENABLE_BOTS" =~ ^[Yy]$ ]]; then
    echo "With bots enabled, HTTP Basic Auth is strongly recommended if this"
    echo "service will be reachable beyond your local machine."
else
    echo "HTTP Basic Auth adds a coarse access gate to the service."
fi
echo
read -r -p "Set up HTTP Basic Auth? [y/N]: " ENABLE_AUTH
ENABLE_AUTH="${ENABLE_AUTH:-N}"
echo

if [[ "$ENABLE_AUTH" =~ ^[Yy]$ ]]; then
    read -r -p "Username: " AUTH_USERNAME
    while [ -z "$AUTH_USERNAME" ]; do
        echo -e "${RED}Username cannot be empty.${NC}"
        read -r -p "Username: " AUTH_USERNAME
    done
    read -r -s -p "Password: " AUTH_PASSWORD
    echo
    while [ -z "$AUTH_PASSWORD" ]; do
        echo -e "${RED}Password cannot be empty.${NC}"
        read -r -s -p "Password: " AUTH_PASSWORD
        echo
    done
    echo -e "${GREEN}Basic Auth configured for user '${AUTH_USERNAME}'.${NC}"
fi
echo

echo -e "${BOLD}─── HTTPS / Snakeoil TLS ────────────────────────────────────────────${NC}"
echo "Generating a local self-signed certificate enables HTTPS-only browser features"
echo "such as the channel key finder and, in some browsers, notifications."
echo "Browsers will still warn that the certificate is untrusted."
echo
read -r -p "Generate and enable a snakeoil TLS certificate? [Y/n]: " ENABLE_SNAKEOIL_TLS
ENABLE_SNAKEOIL_TLS="${ENABLE_SNAKEOIL_TLS:-Y}"
LOCAL_ACCESS_IP="$(detect_primary_local_ip)"
if [[ "$ENABLE_SNAKEOIL_TLS" =~ ^[Yy]$ ]]; then
    ensure_snakeoil_requirements
    generate_snakeoil_certificate "$LOCAL_ACCESS_IP"
    echo -e "${GREEN}Generated snakeoil TLS certificate in ${SNAKEOIL_CERT_DIR}.${NC}"
    echo -e "${YELLOW}Browsers will show an untrusted/self-signed certificate warning.${NC}"
else
    echo -e "${GREEN}Skipping snakeoil TLS generation. The container will serve plain HTTP.${NC}"
fi
echo

if [ "$(uname -s)" = "Linux" ]; then
    echo -e "${BOLD}─── Container User ──────────────────────────────────────────────────${NC}"
    echo "The container runs as root by default for maximum serial compatibility."
    echo "You can override that and run as your host UID/GID instead to avoid"
    echo "root-owned files in ./data."
    echo
    read -r -p "Run as your current UID/GID instead of the default root user? [y/N]: " RUN_AS_HOST_USER
    RUN_AS_HOST_USER="${RUN_AS_HOST_USER:-N}"
    if [[ "$RUN_AS_HOST_USER" =~ ^[Yy]$ ]] && [ "$TRANSPORT_MODE" = "serial" ]; then
        echo
        echo -e "${YELLOW}Note:${NC} host-user mode can be less reliable for serial device access than running as root."
        echo "It may require extra group setup such as dialout, or other manual"
        echo "container customization, depending on your host."
        echo "If serial access becomes unreliable, rerun this setup and keep the"
        echo "default root user instead."
    fi
    echo
fi

mkdir -p "$REPO_DIR/data"

{
    echo "# Generated by scripts/setup/install_docker.sh"
    echo "# This file is gitignored. Re-run the setup script to regenerate it."
    echo "services:"
    echo "  remoteterm:"
    if [ "$IMAGE_MODE" = "build" ]; then
        echo "    build: ."
    else
        echo "    image: docker.io/jkingsman/remoteterm-meshcore:latest"
    fi
    if [[ "$RUN_AS_HOST_USER" =~ ^[Yy]$ ]]; then
        echo "    user: \"$(id -u):$(id -g)\""
    fi
    echo "    ports:"
    echo "      - \"8000:8000\""
    echo "    volumes:"
    echo "      - ./data:/app/data"
    if [[ "$ENABLE_SNAKEOIL_TLS" =~ ^[Yy]$ ]]; then
        echo "      - ./.docker-certs:/app/certs:ro"
    fi
    if [ "$TRANSPORT_MODE" = "serial" ]; then
        echo "    devices:"
        echo "      - ${SERIAL_COMPOSE_HOST_PATH}:${SERIAL_CONTAINER_PATH}"
    fi
    if [[ "$ENABLE_SNAKEOIL_TLS" =~ ^[Yy]$ ]]; then
        echo "    command:"
        echo "      - uv"
        echo "      - run"
        echo "      - uvicorn"
        echo "      - app.main:app"
        echo "      - --host"
        echo "      - 0.0.0.0"
        echo "      - --port"
        echo "      - \"8000\""
        echo "      - --ssl-keyfile"
        echo "      - $SNAKEOIL_KEY_CONTAINER_PATH"
        echo "      - --ssl-certfile"
        echo "      - $SNAKEOIL_CERT_CONTAINER_PATH"
    fi
    echo "    environment:"
    echo "      MESHCORE_DATABASE_PATH: $(yaml_quote "data/meshcore.db")"
    if [ "$TRANSPORT_MODE" = "serial" ]; then
        echo "      MESHCORE_SERIAL_PORT: $(yaml_quote "$SERIAL_CONTAINER_PATH")"
    elif [ "$TRANSPORT_MODE" = "tcp" ]; then
        echo "      MESHCORE_TCP_HOST: $(yaml_quote "$TCP_HOST")"
        echo "      MESHCORE_TCP_PORT: $(yaml_quote "$TCP_PORT")"
    else
        echo "      MESHCORE_BLE_ADDRESS: $(yaml_quote "$BLE_ADDRESS")"
        echo "      MESHCORE_BLE_PIN: $(yaml_quote "$BLE_PIN")"
    fi
    if ! [[ "$ENABLE_BOTS" =~ ^[Yy]$ ]]; then
        echo "      MESHCORE_DISABLE_BOTS: $(yaml_quote "true")"
    fi
    if [[ "$ENABLE_AUTH" =~ ^[Yy]$ ]]; then
        echo "      MESHCORE_BASIC_AUTH_USERNAME: $(yaml_quote "$AUTH_USERNAME")"
        echo "      MESHCORE_BASIC_AUTH_PASSWORD: $(yaml_quote "$AUTH_PASSWORD")"
    fi
    echo "    restart: unless-stopped"
} >"$COMPOSE_FILE"

echo -e "${GREEN}Generated ${COMPOSE_FILE}.${NC}"
echo
echo -e "${BOLD}Docker commands${NC}"
if [ "$IMAGE_MODE" = "build" ]; then
    echo "  sudo docker compose up -d --build    # build the local image and start RemoteTerm in the background"
else
    echo "  sudo docker compose up -d            # start RemoteTerm in the background"
fi
echo "  sudo docker compose logs -f          # follow the container logs live"
echo
echo "  sudo docker compose down             # stop and remove the running container"
echo "  sudo docker compose restart          # restart the container without changing the image"
echo "  sudo docker compose pull && sudo docker compose up -d   # upgrade to the latest published image and restart"
echo
echo -e "${YELLOW}Note:${NC} serial passthrough generally needs ${BOLD}rootful Docker${NC}."
echo "If Docker is running rootless on this host, serial-device mappings may fail even with a valid compose file."
if [ "$TRANSPORT_MODE" = "ble" ] || [ "$BLE_MANUAL_WARNING" = true ]; then
    echo
    echo -e "${RED}BLE requires more than the generated env vars.${NC}"
    echo -e "${RED}Before starting, edit docker-compose.yml for Bluetooth passthrough and any privileged/network settings your host requires.${NC}"
fi
echo
echo -e "${GREEN}Your new docker file is ready at ${COMPOSE_FILE}.${NC}"
echo -e "${GREEN}Feel free to edit it by hand as desired, or:${NC}"
echo
echo -e "${PURPLE}┌───────────────────────────────────────────────┐${NC}"
echo -e "${PURPLE}│ Run ${GREEN}${BOLD}sudo docker compose up -d${NC}${PURPLE} to get started. │${NC}"
echo -e "${PURPLE}└───────────────────────────────────────────────┘${NC}"
if [[ "$ENABLE_SNAKEOIL_TLS" =~ ^[Yy]$ ]]; then
    echo
    echo -e "After the container starts, open ${CYAN}https://${LOCAL_ACCESS_IP}:8000${NC}. Note that this address may change if you use DHCP/have not configured a static IP for your host via your router."
    echo -e "${YELLOW}Expect an untrusted/self-signed certificate warning the first time you connect.${NC}"
else
    echo
    echo -e "After the container starts, open ${CYAN}http://${LOCAL_ACCESS_IP}:8000${NC}. Note that this address may change if you use DHCP/have not configured a static IP for your host via your router."
fi
echo "If the interface does not appear, follow the logs to view errors with:"
echo "  sudo docker compose logs -f"
