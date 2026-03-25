#!/usr/bin/env bash
# RemoteTerm Raspberry Pi management (install / upgrade / uninstall / systemd / SPI or USB LoRa).
# Modeled after pyMC_Repeater manage.sh: whiptail/dialog TUI and optional CLI subcommands.
#
# Usage:
#   sudo ./scripts/manage_remoterm.sh              # interactive menu
#   sudo ./scripts/manage_remoterm.sh install
#   sudo ./scripts/manage_remoterm.sh uninstall
#
# Environment:
#   ALLOW_NON_PI=1     Skip Raspberry Pi device-tree check (for debugging only).
#   FRONTEND_RELEASE_URL  Override prebuilt frontend zip URL (default: meshcore-pi-companion frontend-latest).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_DIR="/opt/remoteterm"
CONFIG_ENV_DIR="/etc/remoterm"
ENV_FILE="$CONFIG_ENV_DIR/environment"
SERVICE_USER="remoteterm"
SERVICE_NAME="remoteterm"
SERVICE_USER_HOME="/var/lib/remoteterm"
DEFAULT_FRONTEND_URL="${FRONTEND_RELEASE_URL:-https://github.com/codemonkeybr/meshcore-pi-companion/releases/download/frontend-latest/frontend-dist.zip}"

# shellcheck disable=SC2034
DIALOG=""

require_tty() {
  if [ ! -t 0 ] || [ -z "${TERM:-}" ]; then
    echo "Error: This script requires an interactive terminal (SSH or local console)." >&2
    exit 1
  fi
}

pick_dialog() {
  if command -v whiptail &>/dev/null; then
    DIALOG="whiptail"
  elif command -v dialog &>/dev/null; then
    DIALOG="dialog"
  else
    echo "TUI requires whiptail or dialog." >&2
    if [ "${EUID:-0}" -eq 0 ]; then
      echo "Installing whiptail..." >&2
      apt-get update -qq
      apt-get install -y whiptail
      DIALOG="whiptail"
    else
      echo "Install with: sudo apt-get install -y whiptail" >&2
      exit 1
    fi
  fi
}

show_info() {
  $DIALOG --backtitle "RemoteTerm Management" --title "$1" --msgbox "$2" 14 72
}

show_error() {
  $DIALOG --backtitle "RemoteTerm Management" --title "Error" --msgbox "$1" 10 64
}

ask_yes_no() {
  $DIALOG --backtitle "RemoteTerm Management" --title "$1" --yesno "$2" 10 72
}

service_exists() {
  systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"
}

is_installed() {
  [ -d "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/pyproject.toml" ]
}

is_running() {
  systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1
}

get_version() {
  if [ -f "$INSTALL_DIR/pyproject.toml" ]; then
    grep "^version" "$INSTALL_DIR/pyproject.toml" | head -1 | cut -d'"' -f2 2>/dev/null || echo "unknown"
  else
    echo "not installed"
  fi
}

get_status_display() {
  if ! is_installed; then
    echo "Not installed"
  elif is_running; then
    echo "Running ($(get_version))"
  else
    echo "Installed but stopped ($(get_version))"
  fi
}

is_raspberry_pi() {
  if [ "${ALLOW_NON_PI:-0}" = "1" ]; then
    return 0
  fi
  if [ ! -f /proc/device-tree/model ]; then
    return 1
  fi
  grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null
}

ensure_pi() {
  if ! is_raspberry_pi; then
    show_error "This installer targets Raspberry Pi only.\n\nSet ALLOW_NON_PI=1 to bypass (unsupported)."
    exit 1
  fi
}

sync_source_to_install() {
  mkdir -p "$INSTALL_DIR"
  if command -v rsync &>/dev/null; then
    rsync -a \
      --exclude='.git' \
      --exclude='.venv' \
      --exclude='frontend/node_modules' \
      --exclude='.mypy_cache' \
      --exclude='.pytest_cache' \
      --exclude='**/__pycache__' \
      "${SOURCE_ROOT}/" "${INSTALL_DIR}/"
  else
    (cd "$SOURCE_ROOT" && tar cf - \
      --exclude='.git' \
      --exclude='.venv' \
      --exclude='frontend/node_modules' \
      .) | (cd "$INSTALL_DIR" && tar xf -)
  fi
}

sync_source_upgrade() {
  mkdir -p "$INSTALL_DIR"
  if command -v rsync &>/dev/null; then
    rsync -a \
      --exclude='.git' \
      --exclude='.venv' \
      --exclude='frontend/node_modules' \
      --exclude='data/meshcore.db' \
      --exclude='data/meshcore.db-*' \
      "${SOURCE_ROOT}/" "${INSTALL_DIR}/"
  else
    echo "rsync is recommended for upgrade; install rsync or re-run install from a fresh copy." >&2
    sync_source_to_install
  fi
}

create_service_user() {
  if ! id "$SERVICE_USER" &>/dev/null; then
    # Pre-create home so useradd does not run skel copy (avoids "Not copying any file from skel" noise).
    mkdir -p "$SERVICE_USER_HOME"
    chmod 755 "$SERVICE_USER_HOME"
    useradd --system --home "$SERVICE_USER_HOME" --shell /sbin/nologin -M "$SERVICE_USER"
  fi
  local g
  for g in dialout spi gpio i2c; do
    if getent group "$g" >/dev/null 2>&1; then
      usermod -a -G "$g" "$SERVICE_USER" 2>/dev/null || true
    fi
  done
}

install_python_deps() {
  # PyPI sometimes closes connections mid-download; retry with backoff. Override timeout: PIP_DEFAULT_TIMEOUT=180.
  sudo -u "$SERVICE_USER" env INSTALL_DIR="$INSTALL_DIR" bash <<'EOS'
set -e
cd "$INSTALL_DIR"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"
pip install --upgrade pip wheel
max=5
attempt=1
while [ "$attempt" -le "$max" ]; do
  if pip install --default-timeout="$PIP_DEFAULT_TIMEOUT" '.[spi]'; then
    break
  fi
  if [ "$attempt" -eq "$max" ]; then
    echo "pip install failed after $max attempts (PyPI timeouts or network). Try again later, use a stable network, or set PIP_INDEX_URL to a mirror." >&2
    exit 1
  fi
  echo "pip install attempt $attempt failed; retrying in $((attempt * 10))s..." >&2
  sleep $((attempt * 10))
  attempt=$((attempt + 1))
done
EOS
}

download_frontend_zip() {
  local dest="$INSTALL_DIR/frontend/frontend-dist.zip"
  mkdir -p "$INSTALL_DIR/frontend"
  if [ -f "$SOURCE_ROOT/frontend/frontend-dist.zip" ]; then
    cp -a "$SOURCE_ROOT/frontend/frontend-dist.zip" "$dest"
    return 0
  fi
  if command -v curl &>/dev/null; then
    curl -fsSL -o "$dest" "$DEFAULT_FRONTEND_URL" && return 0
  fi
  if command -v wget &>/dev/null; then
    wget -q -O "$dest" "$DEFAULT_FRONTEND_URL" && return 0
  fi
  return 1
}

extract_frontend() {
  local zip="$INSTALL_DIR/frontend/frontend-dist.zip"
  if [ ! -f "$zip" ]; then
    return 1
  fi
  mkdir -p "$INSTALL_DIR/frontend/dist"
  if (cd "$INSTALL_DIR/frontend/dist" && unzip -o -q "../frontend-dist.zip"); then
    return 0
  fi
  return 1
}

ensure_spi_in_boot_config() {
  local cfg=""
  if [ -f /boot/firmware/config.txt ]; then
    cfg=/boot/firmware/config.txt
  elif [ -f /boot/config.txt ]; then
    cfg=/boot/config.txt
  fi
  if [ -z "$cfg" ]; then
    show_error "Could not find /boot/config.txt or /boot/firmware/config.txt.\nEnable SPI manually (raspi-config)."
    return 1
  fi
  if grep -q "dtparam=spi=on" "$cfg" 2>/dev/null || grep -q "spi_bcm2835" /proc/modules 2>/dev/null; then
    return 0
  fi
  if ask_yes_no "SPI not enabled" "SPI is required for the LoRa HAT.\n\nEnable dtparam=spi=on in\n$cfg\n\n(Reboot required.)"; then
    echo "dtparam=spi=on" >>"$cfg"
    show_info "SPI" "SPI enabled in $cfg.\n\nReboot, then run this installer again."
    reboot
  else
    show_error "SPI is required for onboard LoRa (SPI mode)."
    return 1
  fi
}

clear_usb_environment_file() {
  mkdir -p "$CONFIG_ENV_DIR"
  rm -f "$ENV_FILE"
}

write_usb_environment_file() {
  local port="$1"
  mkdir -p "$CONFIG_ENV_DIR"
  umask 022
  cat >"$ENV_FILE" <<EOF
# Managed by manage_remoterm.sh — USB serial MeshCore radio
MESHCORE_SERIAL_PORT=$port
EOF
  chmod 644 "$ENV_FILE"
}

backup_and_remove_spi_configs() {
  local ts
  ts="$(date +%Y%m%d_%H%M%S)"
  for f in "$INSTALL_DIR/data/config.yaml" "$INSTALL_DIR/config.yaml"; do
    if [ -f "$f" ]; then
      cp -a "$f" "${f}.bak_usb_${ts}"
      rm -f "$f"
    fi
  done
}

run_spi_wizard() {
  sudo -u "$SERVICE_USER" env \
    HOME="$SERVICE_USER_HOME" \
    PYTHONPATH="$INSTALL_DIR" \
    bash -c "cd '$INSTALL_DIR' && exec '$INSTALL_DIR/.venv/bin/python' -m app.setup_cli --config-out data/config.yaml"
}

install_systemd_unit() {
  cp "$INSTALL_DIR/remoteterm.service" /etc/systemd/system/remoteterm.service
  systemctl daemon-reload
}

configure_usb_port_interactive() {
  local opts=()
  local dev
  for dev in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$dev" ] || continue
    opts+=("$dev" "$dev")
  done
  if [ "${#opts[@]}" -eq 0 ]; then
    local manual
    manual=$($DIALOG --inputbox "No /dev/ttyUSB* or /dev/ttyACM* found.\nEnter serial device path:" 12 60 "/dev/ttyUSB0" 3>&1 1>&2 2>&3) || true
    echo "${manual:-/dev/ttyUSB0}"
    return
  fi
  # shellcheck disable=SC2086
  local choice
  choice=$($DIALOG --menu "Select USB serial device" 20 70 10 "${opts[@]}" 3>&1 1>&2 2>&3) || true
  if [ -n "${choice:-}" ]; then
    echo "$choice"
  else
    echo "/dev/ttyUSB0"
  fi
}

do_install() {
  require_tty
  pick_dialog
  ensure_pi

  if [ "${EUID:-0}" -ne 0 ]; then
    show_error "Installation requires root.\n\nRun: sudo $0 install"
    exit 1
  fi

  if is_installed; then
    show_error "RemoteTerm is already installed under $INSTALL_DIR.\n\nUse upgrade or uninstall first."
    exit 1
  fi

  $DIALOG --backtitle "RemoteTerm Management" --title "Welcome" --msgbox \
    "This installer sets up RemoteTerm on Raspberry Pi OS.\n\nYou will choose:\n- SPI + LoRa HAT, or\n- USB MeshCore radio\n\nThen dependencies, optional frontend zip, and systemd." 14 72

  local transport
  transport=$($DIALOG --menu "Transport" 15 70 2 \
    spi "SPI LoRa HAT (onboard radio, data/config.yaml)" \
    usb "USB serial MeshCore radio" \
    3>&1 1>&2 2>&3) || exit 1

  if [ "$transport" = "spi" ]; then
    ensure_spi_in_boot_config || exit 1
  fi

  (
    echo "5"
    echo "# Creating service user..."
    create_service_user
    echo "15"
    echo "# Copying application to $INSTALL_DIR..."
    sync_source_to_install
    echo "35"
    echo "# Installing Python dependencies..."
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    install_python_deps
    echo "55"
    echo "# Frontend zip..."
    if download_frontend_zip; then
      sudo -u "$SERVICE_USER" bash -c "cd '$INSTALL_DIR' && mkdir -p frontend/dist && (cd frontend/dist && unzip -o -q ../frontend-dist.zip)" || true
    fi
    echo "75"
    echo "# Radio configuration..."
  ) | $DIALOG --gauge "Installing RemoteTerm..." 8 70 0

  if [ "$transport" = "spi" ]; then
    clear_usb_environment_file
    require_tty
    show_info "SPI wizard" "The SPI setup wizard will run in the terminal.\n\nDefaults: node name, hardware profile, preset, optional GPS."
    clear
    if run_spi_wizard; then
      :
    else
      show_error "SPI wizard exited with an error.\n\nFix issues and run:\n  sudo $0 config-spi"
    fi
  else
    backup_and_remove_spi_configs
    local port
    port="$(configure_usb_port_interactive)"
    write_usb_environment_file "$port"
  fi

  install_systemd_unit
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME" || systemctl start "$SERVICE_NAME"

  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  show_info "Done" "RemoteTerm installed.\n\nWeb UI: http://${ip:-localhost}:8000\n\nLogs: sudo journalctl -u $SERVICE_NAME -f"
}

do_upgrade() {
  require_tty
  pick_dialog
  if [ "${EUID:-0}" -ne 0 ]; then
    show_error "Upgrade requires root.\n\nRun: sudo $0 upgrade"
    exit 1
  fi
  if ! is_installed; then
    show_error "RemoteTerm is not installed."
    exit 1
  fi
  if ! ask_yes_no "Confirm upgrade" "Upgrade RemoteTerm from:\n$SOURCE_ROOT\n\nto $INSTALL_DIR ?"; then
    exit 0
  fi
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  local backup_dir="/tmp/remoteterm_data_backup_$(date +%Y%m%d_%H%M%S)"
  if [ -d "$INSTALL_DIR/data" ]; then
    cp -a "$INSTALL_DIR/data" "$backup_dir" 2>/dev/null || true
  fi
  sync_source_upgrade
  chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
  install_python_deps
  install_systemd_unit
  systemctl start "$SERVICE_NAME"
  show_info "Upgrade" "Upgrade complete.\n\nData backup copy:\n$backup_dir"
}

do_uninstall() {
  require_tty
  pick_dialog
  if [ "${EUID:-0}" -ne 0 ]; then
    show_error "Uninstall requires root.\n\nRun: sudo $0 uninstall"
    exit 1
  fi
  if ! ask_yes_no "Confirm uninstall" "Remove RemoteTerm service, $INSTALL_DIR, $CONFIG_ENV_DIR, and $SERVICE_USER_HOME ?\n\nBackups are copied to /tmp before removal."; then
    exit 0
  fi
  (
    echo "10"
    echo "# Stopping service..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    echo "30"
    echo "# Backup..."
    if [ -d "$CONFIG_ENV_DIR" ]; then
      cp -a "$CONFIG_ENV_DIR" "/tmp/remoteterm_etc_backup_$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
    fi
    if [ -d "$SERVICE_USER_HOME" ]; then
      cp -a "$SERVICE_USER_HOME" "/tmp/remoteterm_varlib_backup_$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
    fi
    echo "50"
    echo "# Removing unit..."
    rm -f /etc/systemd/system/remoteterm.service
    systemctl daemon-reload
    echo "70"
    echo "# Removing files..."
    rm -rf "$INSTALL_DIR"
    rm -rf "$CONFIG_ENV_DIR"
    rm -rf "$SERVICE_USER_HOME"
    echo "90"
    echo "# Removing service user (if safe)..."
    if id "$SERVICE_USER" &>/dev/null; then
      userdel "$SERVICE_USER" 2>/dev/null || true
    fi
    echo "100"
  ) | $DIALOG --gauge "Uninstalling..." 8 70 0
  show_info "Uninstall" "RemoteTerm removed.\n\nBackups under /tmp/ (remoteterm_etc_backup_*, remoteterm_varlib_backup_*)."
}

do_configure_spi() {
  require_tty
  pick_dialog
  if [ "${EUID:-0}" -ne 0 ]; then
    show_error "Root required (sudo)."
    exit 1
  fi
  if [ ! -x "$INSTALL_DIR/.venv/bin/python" ]; then
    show_error "RemoteTerm is not installed under $INSTALL_DIR (missing venv).\n\nRun: sudo $0 install"
    exit 1
  fi
  if [ ! -f "$INSTALL_DIR/config.yaml.example" ]; then
    show_error "Missing $INSTALL_DIR/config.yaml.example — install tree is incomplete."
    exit 1
  fi
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  clear
  run_spi_wizard || true
  clear_usb_environment_file
  systemctl start "$SERVICE_NAME" 2>/dev/null || true
  show_info "Configure" "SPI configuration updated."
}

do_configure_usb() {
  require_tty
  pick_dialog
  if [ "${EUID:-0}" -ne 0 ]; then
    show_error "Root required (sudo)."
    exit 1
  fi
  if ! is_installed; then
    show_error "RemoteTerm is not installed under $INSTALL_DIR."
    exit 1
  fi
  local port
  port="$(configure_usb_port_interactive)"
  write_usb_environment_file "$port"
  backup_and_remove_spi_configs
  systemctl restart "$SERVICE_NAME" 2>/dev/null || true
  show_info "USB" "Serial port set to $port in $ENV_FILE.\n\nService restarted."
}

manage_service() {
  local action="$1"
  if [ "${EUID:-0}" -ne 0 ]; then
    show_error "Service control requires root."
    return
  fi
  if ! service_exists; then
    show_error "Service is not installed."
    return
  fi
  case "$action" in
    start) systemctl start "$SERVICE_NAME" ;;
    stop) systemctl stop "$SERVICE_NAME" ;;
    restart) systemctl restart "$SERVICE_NAME" ;;
  esac
}

manage_service_cli() {
  local action="$1"
  if [ "${EUID:-0}" -ne 0 ]; then
    echo "Error: root required (sudo)." >&2
    exit 1
  fi
  if ! service_exists; then
    echo "Error: ${SERVICE_NAME}.service is not installed." >&2
    exit 1
  fi
  systemctl "$action" "$SERVICE_NAME"
}

show_detailed_status() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  local msg=""
  msg="Status: $(get_status_display)\n"
  msg+="Install: $INSTALL_DIR\n"
  msg+="Env file: $ENV_FILE\n"
  msg+="IP: ${ip:-unknown}\n"
  if grep -q "spi_bcm2835" /proc/modules 2>/dev/null; then
    msg+="SPI kernel module: loaded\n"
  else
    msg+="SPI kernel module: not loaded\n"
  fi
  show_info "Status" "$msg"
}

show_main_menu() {
  local status
  status="$(get_status_display)"
  local choice
  choice=$($DIALOG --backtitle "RemoteTerm Management" --title "RemoteTerm" --menu \
    "Current: $status\nChoose:" 22 72 12 \
    install "Install to $INSTALL_DIR" \
    upgrade "Upgrade from this source tree" \
    uninstall "Remove RemoteTerm" \
    config_spi "Run SPI setup wizard" \
    config_usb "Set USB serial port" \
    start "Start service" \
    stop "Stop service" \
    restart "Restart service" \
    logs "Live logs (journalctl)" \
    status "Detailed status" \
    exit "Exit" \
    3>&1 1>&2 2>&3) || exit 0

  case "$choice" in
    install) do_install ;;
    upgrade) do_upgrade ;;
    uninstall) do_uninstall ;;
    config_spi) do_configure_spi ;;
    config_usb) do_configure_usb ;;
    start) pick_dialog; manage_service start ;;
    stop) pick_dialog; manage_service stop ;;
    restart) pick_dialog; manage_service restart ;;
    logs)
      clear
      echo "=== journalctl -u $SERVICE_NAME -f (Ctrl+C to exit) ==="
      journalctl -u "$SERVICE_NAME" -f
      ;;
    status) pick_dialog; show_detailed_status ;;
    exit) exit 0 ;;
  esac
}

print_help() {
  cat <<EOF
RemoteTerm Pi management (Raspberry Pi OS).

Usage: $0 [command]

Commands:
  install     Full install (interactive transport selection)
  upgrade     Sync from source tree to $INSTALL_DIR
  uninstall   Remove service and install directory
  config-spi  Run SPI wizard (setup_cli)
  config-usb  Set USB serial device in $ENV_FILE
  start | stop | restart   systemd
  status      Show status
  help        This help

With no arguments, shows the interactive menu (requires a TTY).

Environment:
  ALLOW_NON_PI=1
  FRONTEND_RELEASE_URL=...   Prebuilt frontend zip URL
EOF
}

# ---- CLI (non-interactive where possible) ----
case "${1:-}" in
  help|-h|--help)
    print_help
    exit 0
    ;;
  install)
    do_install
    exit 0
    ;;
  upgrade)
    do_upgrade
    exit 0
    ;;
  uninstall)
    do_uninstall
    exit 0
    ;;
  config-spi)
    do_configure_spi
    exit 0
    ;;
  config-usb)
    do_configure_usb
    exit 0
    ;;
  start|stop|restart)
    manage_service_cli "$1"
    exit 0
    ;;
  status)
    if is_installed; then
      systemctl status "$SERVICE_NAME" 2>/dev/null || true
    else
      echo "RemoteTerm is not installed under $INSTALL_DIR."
    fi
    exit 0
    ;;
esac

require_tty
pick_dialog
while true; do
  show_main_menu
done
