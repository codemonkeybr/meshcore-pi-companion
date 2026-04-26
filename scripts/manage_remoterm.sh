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
#   REMOTETERM_REPO=codemonkeybr/meshcore-pi-companion  GitHub repo for release downloads.
#   RELEASE_VERSION=latest  Release tag for upgrade (overridden by --version flag).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_DIR="${REMOTETERM_INSTALL_DIR:-/opt/remoteterm}"
CONFIG_ENV_DIR="/etc/remoterm"
ENV_FILE="$CONFIG_ENV_DIR/environment"
SERVICE_USER="remoteterm"
SERVICE_NAME="remoteterm"
SERVICE_USER_HOME="/var/lib/remoteterm"
REMOTETERM_REPO="${REMOTETERM_REPO:-codemonkeybr/meshcore-pi-companion}"
DEFAULT_FRONTEND_URL="${FRONTEND_RELEASE_URL:-https://github.com/${REMOTETERM_REPO}/releases/download/frontend-latest/frontend-dist.zip}"

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
  # Prefer the unit file path (reliable on all systemd versions); list-unit-files grep is unreliable.
  [ -f "/etc/systemd/system/${SERVICE_NAME}.service" ] ||
    [ -f "/lib/systemd/system/${SERVICE_NAME}.service" ] ||
    [ -f "/usr/lib/systemd/system/${SERVICE_NAME}.service" ]
}

is_installed() {
  [ -d "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/pyproject.toml" ]
}

is_running() {
  systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1
}

is_frontend_installed() {
  [ -f "$INSTALL_DIR/frontend/dist/index.html" ]
}

get_install_state() {
  if ! is_installed; then
    echo "nothing_installed"
  elif is_frontend_installed; then
    echo "backend_and_frontend"
  else
    echo "backend_only"
  fi
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

# http_get_text URL -- print URL body to stdout (silent, for API queries).
http_get_text() {
  local url="$1"
  if command -v curl &>/dev/null; then
    curl -fsSL "$url"
  elif command -v wget &>/dev/null; then
    wget -q -O- "$url"
  else
    echo "Error: need curl or wget." >&2
    return 1
  fi
}

# http_fetch URL OUTFILE -- download URL to OUTFILE.
http_fetch() {
  local url="$1" out="$2"
  if command -v curl &>/dev/null; then
    if [ -t 1 ]; then
      curl -fL --progress-bar -o "$out" "$url"
    else
      curl -fsSL -o "$out" "$url"
    fi
  elif command -v wget &>/dev/null; then
    wget -q --show-progress -O "$out" "$url"
  else
    echo "Error: need curl or wget." >&2
    return 1
  fi
}

# Resolve "latest" or a specific tag against the GitHub Releases API. Echoes the
# concrete tag (e.g. "v3.2.0") on success.
resolve_release_version() {
  local version="$1"
  if [ -z "$version" ] || [ "$version" = "latest" ]; then
    local body
    if ! body="$(http_get_text "https://api.github.com/repos/${REMOTETERM_REPO}/releases/latest")"; then
      echo "Error: failed to query GitHub Releases API for the latest version." >&2
      return 1
    fi
    local tag
    tag="$(printf '%s' "$body" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
    if [ -z "$tag" ]; then
      echo "Error: could not parse tag_name from GitHub API response." >&2
      return 1
    fi
    echo "$tag"
    return 0
  fi
  # Validate the tag exists by asking the API directly.
  if ! http_get_text "https://api.github.com/repos/${REMOTETERM_REPO}/releases/tags/${version}" >/dev/null; then
    echo "Error: release version '$version' not found in ${REMOTETERM_REPO}." >&2
    return 1
  fi
  echo "$version"
}

# Download + extract a release tarball. Echoes the extracted source root path.
# Usage: download_release_to_tmp TAG
download_release_to_tmp() {
  local tag="$1"
  local semver="${tag#v}"
  local artifact="remoteterm-backend-${semver}.tar.gz"
  local checksum="${artifact}.sha256"
  local base="https://github.com/${REMOTETERM_REPO}/releases/download/${tag}"
  local tmp
  tmp="$(mktemp -d /tmp/remoteterm-upgrade-XXXXXX)"

  echo "Downloading $artifact ..." >&2
  if ! http_fetch "${base}/${artifact}" "${tmp}/${artifact}"; then
    echo "Error: failed to download ${base}/${artifact}" >&2
    rm -rf "$tmp"
    return 1
  fi

  if http_fetch "${base}/${checksum}" "${tmp}/${checksum}" 2>/dev/null; then
    echo "Verifying SHA256 checksum..." >&2
    if ! (cd "$tmp" && sha256sum -c "$checksum" >/dev/null 2>&1); then
      echo "Error: checksum verification failed for $artifact." >&2
      rm -rf "$tmp"
      return 1
    fi
  else
    echo "Warning: no checksum file for $tag; skipping integrity verification." >&2
  fi

  mkdir -p "${tmp}/src"
  if ! tar -xzf "${tmp}/${artifact}" -C "${tmp}/src"; then
    echo "Error: failed to extract ${artifact}." >&2
    rm -rf "$tmp"
    return 1
  fi

  if [ ! -f "${tmp}/src/pyproject.toml" ]; then
    echo "Error: release artifact missing pyproject.toml — malformed bundle." >&2
    rm -rf "$tmp"
    return 1
  fi
  echo "${tmp}/src"
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
pip install --no-cache-dir --upgrade pip wheel
max=5
attempt=1
while [ "$attempt" -le "$max" ]; do
  if pip install --no-cache-dir --default-timeout="$PIP_DEFAULT_TIMEOUT" '.[spi]'; then
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

extract_frontend_atomic() {
  local zip="$INSTALL_DIR/frontend/frontend-dist.zip"
  if [ ! -f "$zip" ]; then
    return 1
  fi
  local dist="$INSTALL_DIR/frontend/dist"
  local dist_tmp="${dist}.tmp"
  local dist_old="${dist}.old"

  rm -rf "$dist_tmp"
  mkdir -p "$dist_tmp"

  if ! (cd "$dist_tmp" && unzip -o -q "$zip"); then
    rm -rf "$dist_tmp"
    return 1
  fi

  if [ -d "$dist" ]; then
    rm -rf "$dist_old"
    mv "$dist" "$dist_old"
  fi
  mv "$dist_tmp" "$dist"
  rm -rf "$dist_old" 2>/dev/null || true
  return 0
}

install_frontend_bundle() {
  if download_frontend_zip; then
    if extract_frontend_atomic; then
      return 0
    fi
    echo "Error: failed to extract frontend bundle." >&2
    return 1
  fi
  echo "Error: failed to download frontend bundle." >&2
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
  local components=""
  local requested_version="${RELEASE_VERSION:-latest}"

  # Parse arguments (T005 / FR-015)
  while [ $# -gt 0 ]; do
    case "$1" in
      --components)
        if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
          echo "Error: --components requires a value (be, be+fe, fe)." >&2
          exit 2
        fi
        components="$2"
        shift 2
        ;;
      --components=*)
        components="${1#--components=}"
        shift
        ;;
      --version)
        if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
          echo "Error: --version requires a value (e.g. --version v3.2.0)." >&2
          exit 2
        fi
        requested_version="$2"
        shift 2
        ;;
      --version=*)
        requested_version="${1#--version=}"
        shift
        ;;
      *)
        echo "Error: unknown install argument: $1" >&2
        exit 2
        ;;
    esac
  done

  # Validate --components value early (T005)
  case "${components:-}" in
    be | be+fe | fe | "") ;;
    *)
      echo "Error: --components ${components}: unknown value. Accepted: be, be+fe, fe." >&2
      exit 2
      ;;
  esac

  # Non-interactive mode requires --components (T011 / FR-015)
  if [ -z "$components" ] && { [ ! -t 0 ] || [ -z "${TERM:-}" ]; }; then
    echo "Error: --components is required in non-interactive mode. Accepted values: be, be+fe, fe." >&2
    exit 1
  fi

  # FR-006: Refuse fe-only when backend not installed — checked before TTY requirement
  # so automation also gets a clear message without needing a terminal.
  if [ "$components" = "fe" ] && ! is_installed; then
    echo "Error: cannot install frontend — backend is not installed. Run: sudo $0 install --components be (or be+fe) first." >&2
    exit 1
  fi

  require_tty
  pick_dialog
  ensure_pi

  if [ "${EUID:-0}" -ne 0 ]; then
    show_error "Installation requires root.\n\nRun: sudo $0 install"
    exit 1
  fi

  # Detect current state (T013 / FR-002)
  local state
  state="$(get_install_state)"

  # Determine component selection interactively when not provided (T010)
  if [ -z "$components" ]; then
    case "$state" in
      nothing_installed)
        components=$(
          $DIALOG --backtitle "RemoteTerm Management" --title "Component Selection" \
            --menu "What would you like to install?" 15 72 2 \
            be     "Backend only (API service, no web UI)" \
            be+fe  "Backend + Frontend (API service + web UI)" \
            3>&1 1>&2 2>&3
        ) || exit 0
        ;;
      backend_only)
        components=$(
          $DIALOG --backtitle "RemoteTerm Management" --title "Component Selection" \
            --menu "Backend is already installed. What would you like to do?" 15 72 2 \
            fe     "Add Frontend (web UI) to existing backend" \
            cancel "Cancel" \
            3>&1 1>&2 2>&3
        ) || exit 0
        [ "$components" = "cancel" ] && exit 0
        ;;
      backend_and_frontend)
        show_error "RemoteTerm is already fully installed (Backend + Frontend).\n\nUse upgrade or uninstall."
        exit 1
        ;;
    esac
  fi

  # Validate component selection against current state (T007 / T012 / T016 / T017)
  case "$components" in
    be | be+fe)
      if [ "$state" != "nothing_installed" ]; then
        show_error "RemoteTerm is already installed under $INSTALL_DIR.\n\nUse upgrade or uninstall first."
        exit 1
      fi
      ;;
    fe)
      case "$state" in
        nothing_installed)
          # Show error via dialog if TTY available, also print to stderr for automation
          echo "Error: cannot install frontend — backend is not installed. Run: sudo $0 install --components be (or be+fe) first." >&2
          show_error "Cannot install frontend — backend is not installed.\n\nRun install first:\n  sudo $0 install --components be+fe"
          exit 1
          ;;
        backend_and_frontend)
          if ! ask_yes_no "Frontend Already Installed" "The frontend is already installed.\n\nRefresh it with the latest release?"; then
            exit 0
          fi
          ;;
        backend_only)
          # Valid — proceed
          ;;
      esac
      ;;
  esac

  # Pre-action confirmation summary (T006 / FR-012)
  local action_summary=""
  case "$components" in
    be)    action_summary="Install: Backend (API service)\nSkip:    Frontend (web UI)" ;;
    be+fe) action_summary="Install: Backend (API service)\nInstall: Frontend (web UI)" ;;
    fe)    action_summary="Add:     Frontend (web UI)\nPreserve: Existing backend (unchanged)" ;;
  esac
  if ! ask_yes_no "Confirm Installation" "Planned actions:\n\n$action_summary\n\nProceed?"; then
    exit 0
  fi

  # ── Frontend-only path (T015 / US2) ────────────────────────────────────────
  if [ "$components" = "fe" ]; then
    (
      echo "20"
      echo "# Downloading frontend bundle..."
      install_frontend_bundle >/dev/null 2>&1
      echo "80"
      echo "# Restarting service..."
      systemctl restart "$SERVICE_NAME" >/dev/null 2>&1 || true
      echo "100"
    ) | $DIALOG --gauge "Installing Frontend..." 8 70 0

    if [ ! -f "$INSTALL_DIR/frontend/dist/index.html" ]; then
      show_error "Failed to install frontend.\n\nCheck network access and try again."
      exit 1
    fi

    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    show_info "Frontend Installed" "Frontend installed under $INSTALL_DIR/frontend/dist/.\n\nService restarted — web UI is now live.\n\nWeb UI: http://${ip:-localhost}:8000"
    return 0
  fi

  # ── Backend install path (T008 be / T009 be+fe / US1) ──────────────────────
  $DIALOG --backtitle "RemoteTerm Management" --title "Welcome" --msgbox \
    "This installer sets up RemoteTerm on Raspberry Pi OS.\n\nYou will choose:\n- SPI + LoRa HAT, or\n- USB MeshCore radio\n\nThen dependencies, systemd, and optional web UI." 14 72

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
    create_service_user >/dev/null 2>&1
    echo "15"
    echo "# Copying application to $INSTALL_DIR..."
    sync_source_to_install >/dev/null 2>&1
    echo "35"
    echo "# Installing Python dependencies..."
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" 2>/dev/null
    install_python_deps >/dev/null 2>&1
    echo "55"
    if [ "$components" = "be+fe" ]; then
      echo "# Installing frontend bundle..."
      install_frontend_bundle >/dev/null 2>&1 || true
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

  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  local ui_note=""
  if [ "$components" = "be+fe" ]; then
    ui_note="\n\nWeb UI (after start): http://${ip:-localhost}:8000"
  else
    ui_note="\n\n(Frontend not installed — API only. Add later with: sudo $0 install --components fe)"
  fi
  show_info "Done" "RemoteTerm installed under $INSTALL_DIR.\n\nThe service is enabled for boot but was not started yet.\n\nStart it when ready:\n  sudo systemctl start $SERVICE_NAME\nor choose Start from this menu.${ui_note}"
}

do_upgrade() {
  local requested_version="${RELEASE_VERSION:-latest}"
  while [ $# -gt 0 ]; do
    case "$1" in
      --version)
        if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
          echo "Error: --version requires a value (e.g. --version v3.2.0)." >&2
          exit 2
        fi
        requested_version="$2"
        shift 2
        ;;
      --version=*)
        requested_version="${1#--version=}"
        shift
        ;;
      *)
        echo "Error: unknown upgrade argument: $1" >&2
        exit 2
        ;;
    esac
  done

  # Detect state before requiring TTY so automation gets a clear message (FR-008)
  local state
  state="$(get_install_state)"

  # T022: Exit with clear message if nothing installed (FR-008)
  if [ "$state" = "nothing_installed" ]; then
    echo "Error: RemoteTerm is not installed. Run: sudo $0 install" >&2
    exit 1
  fi

  require_tty
  pick_dialog
  if [ "${EUID:-0}" -ne 0 ]; then
    show_error "Upgrade requires root.\n\nRun: sudo $0 upgrade"
    exit 1
  fi

  echo "Resolving release version ($requested_version)..."
  local tag
  if ! tag="$(resolve_release_version "$requested_version")"; then
    show_error "Could not resolve release version '$requested_version'.\n\nCheck network access and verify the tag exists on\nhttps://github.com/${REMOTETERM_REPO}/releases"
    exit 1
  fi

  local current_version
  current_version="$(get_version)"

  # Pre-action confirmation naming which components will be upgraded (FR-012)
  local component_note=""
  if [ "$state" = "backend_and_frontend" ]; then
    component_note="\nComponents: Backend + Frontend"
  else
    component_note="\nComponents: Backend only (no frontend installed)"
  fi
  if ! ask_yes_no "Confirm upgrade" "Upgrade RemoteTerm:\n\n  current : $current_version\n  target  : ${tag#v}${component_note}\n\nDownload release and install to $INSTALL_DIR ?"; then
    exit 0
  fi

  local release_src
  if ! release_src="$(download_release_to_tmp "$tag")"; then
    show_error "Failed to download release $tag.\n\nSee terminal output for details."
    exit 1
  fi

  local backup_dir="/tmp/remoteterm_data_backup_$(date +%Y%m%d_%H%M%S)"
  local fe_flag_file
  fe_flag_file="$(mktemp)"
  echo "0" > "$fe_flag_file"

  (
    echo "10"
    echo "# Stopping service..."
    systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true

    echo "20"
    echo "# Backing up data..."
    if [ -d "$INSTALL_DIR/data" ]; then
      cp -a "$INSTALL_DIR/data" "$backup_dir" >/dev/null 2>&1 || true
    fi

    echo "35"
    echo "# Syncing application files..."
    SOURCE_ROOT="$release_src"
    sync_source_upgrade >/dev/null 2>&1

    echo "55"
    echo "# Installing Python dependencies..."
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" 2>/dev/null
    install_python_deps >/dev/null 2>&1
    install_systemd_unit >/dev/null 2>&1

    # T019/T020/T021: Upgrade frontend only when it was installed (FR-008, FR-009)
    if [ "$state" = "backend_and_frontend" ]; then
      echo "75"
      echo "# Upgrading frontend bundle..."
      # T023: Partial-failure handling — FE failure does not abort BE upgrade
      if ! install_frontend_bundle >/dev/null 2>&1; then
        echo "1" > "$fe_flag_file"
      fi
    fi

    echo "90"
    echo "# Starting service..."
    systemctl start "$SERVICE_NAME" >/dev/null 2>&1 || true

    echo "100"
  ) | $DIALOG --gauge "Upgrading RemoteTerm..." 8 70 0

  local fe_result
  fe_result="$(cat "$fe_flag_file")"
  rm -f "$fe_flag_file"

  rm -rf "$(dirname "$release_src")" 2>/dev/null || true

  if [ "$fe_result" -ne 0 ]; then
    show_info "Upgrade (partial)" "Backend upgraded to ${tag#v}.\n\nFrontend upgrade FAILED — previous UI remains intact.\nCheck network access and retry upgrade.\n\nData backup copy:\n$backup_dir"
  else
    show_info "Upgrade" "Upgrade complete.\n\nInstalled: ${tag#v}\nData backup copy:\n$backup_dir"
  fi
}

do_uninstall() {
  # T024: Detect current state before requiring TTY (FR-002)
  local state
  state="$(get_install_state)"

  # T027: Exit cleanly if nothing installed — checked before TTY requirement (spec AC3)
  if [ "$state" = "nothing_installed" ]; then
    echo "Error: RemoteTerm is not installed — nothing to remove." >&2
    exit 1
  fi

  require_tty
  pick_dialog
  if [ "${EUID:-0}" -ne 0 ]; then
    show_error "Uninstall requires root.\n\nRun: sudo $0 uninstall"
    exit 1
  fi

  # T026: Component summary in confirmation (FR-012)
  local component_note=""
  if [ "$state" = "backend_and_frontend" ]; then
    component_note="Backend + Frontend"
  else
    component_note="Backend only"
  fi
  if ! ask_yes_no "Confirm uninstall" "Remove RemoteTerm ($component_note):\n\n  $INSTALL_DIR\n  $CONFIG_ENV_DIR\n  $SERVICE_USER_HOME\n\nBackups are copied to /tmp before removal."; then
    exit 0
  fi

  # T025: Remove whatever is installed; silently skip missing components (FR-010, FR-011)
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
    show_error "systemd unit not found (expected /etc/systemd/system/${SERVICE_NAME}.service).\n\nRun install first, or copy remoteterm.service and run: sudo systemctl daemon-reload"
    return
  fi
  case "$action" in
    start)
      if systemctl start "$SERVICE_NAME" 2>/dev/null; then
        show_info "Start" "Service started.\n\nStatus: $(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo unknown)"
      else
        show_error "Failed to start the service.\n\nsudo journalctl -u $SERVICE_NAME -n 50"
      fi
      ;;
    stop)
      if systemctl stop "$SERVICE_NAME" 2>/dev/null; then
        show_info "Stop" "Service stopped.\n\nStatus: $(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo unknown)"
      else
        show_error "Failed to stop the service."
      fi
      ;;
    restart)
      if systemctl restart "$SERVICE_NAME" 2>/dev/null; then
        show_info "Restart" "Service restarted.\n\nStatus: $(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo unknown)"
      else
        show_error "Failed to restart the service.\n\nsudo journalctl -u $SERVICE_NAME -n 50"
      fi
      ;;
  esac
}

manage_service_cli() {
  local action="$1"
  if [ "${EUID:-0}" -ne 0 ]; then
    echo "Error: root required (sudo)." >&2
    exit 1
  fi
  if ! service_exists; then
    echo "Error: ${SERVICE_NAME}.service not found under /etc/systemd/system/ (run install first)." >&2
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
  install [--components be|be+fe|fe] [--version vX.Y.Z]
                      Install RemoteTerm components.
                        be      Backend only (API service, no web UI)
                        be+fe   Backend + Frontend (API + web UI)  [default interactive]
                        fe      Frontend only (add to an existing backend install)
                      --version defaults to latest.
                      In non-interactive mode (no TTY), --components is required.
  upgrade [--version vX.Y.Z]
                      Upgrade whatever is currently installed.
                      Detects installed components automatically:
                        Backend-only installs: backend is upgraded, frontend untouched.
                        Backend+Frontend installs: both components are upgraded.
                      Defaults to latest when --version is omitted.
  uninstall           Remove all installed components (backend + frontend if present).
                      Detects installed components automatically.
  config-spi          Run SPI wizard (setup_cli)
  config-usb          Set USB serial device in $ENV_FILE
  start | stop | restart   systemd
  status              Show status
  help                This help

With no arguments, shows the interactive menu (requires a TTY).

Environment:
  ALLOW_NON_PI=1
  FRONTEND_RELEASE_URL=...   Prebuilt frontend zip URL
  REMOTETERM_REPO=owner/name GitHub repo for release downloads
  RELEASE_VERSION=latest     Default release tag for upgrade
EOF
}

# ---- CLI (non-interactive where possible) ----
case "${1:-}" in
  help|-h|--help)
    print_help
    exit 0
    ;;
  install)
    shift
    do_install "$@"
    exit 0
    ;;
  upgrade)
    shift
    do_upgrade "$@"
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
