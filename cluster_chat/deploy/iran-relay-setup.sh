#!/usr/bin/env bash
# ==============================================================================
# Cluster Chat - Domestic Iranian Relay Setup Script (نت ملی)
# Idempotent forwarder installation for socat (Layer-4) and Nginx (Layer-7)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="cluster-chat-relay"

# Default configurations
FOREIGN_IP=""
FOREIGN_PORT="8080"
LOCAL_PORT="8080"
MODE="socat"  # socat | nginx
DRY_RUN=false
SYSTEMD_DIR="/etc/systemd/system"
NGINX_CONF_DIR="/etc/nginx/conf.d"

# Styling & Logging
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_err() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

print_usage() {
    cat <<EOF
Cluster Chat Domestic Iranian Relay Setup (راه‌اندازی سرور رله داخلی برای شرایط نت ملی)

Usage:
  $(basename "$0") --foreign-ip <IP> [options]

Options:
  --foreign-ip <IP>        Foreign VPS IP address running Cluster Chat (required)
  --foreign-port <PORT>    Target Cluster Chat port on foreign VPS (default: 8080)
  --local-port <PORT>      Local listener port on Iranian relay VPS (default: 8080)
  --mode <socat|nginx>     Relay forwarding mode (default: socat)
                             socat: Low-overhead Layer-4 TCP forwarder via systemd
                             nginx: Layer-7 reverse proxy with header preservation
  --dry-run                Print planned actions and files without modifying system
  --systemd-dir <DIR>      Custom systemd directory (default: /etc/systemd/system)
  --nginx-dir <DIR>        Custom Nginx conf.d directory (default: /etc/nginx/conf.d)
  -h, --help               Show this help message and exit

Environment Variables:
  FOREIGN_VPS_IP           Alternative to --foreign-ip
  FOREIGN_VPS_PORT         Alternative to --foreign-port
  LOCAL_RELAY_PORT         Alternative to --local-port
  RELAY_MODE               Alternative to --mode

Examples:
  # 1. Setup Layer-4 socat relay forwarder
  sudo ./iran-relay-setup.sh --foreign-ip 203.0.113.10 --mode socat

  # 2. Setup Layer-7 Nginx reverse proxy
  sudo ./iran-relay-setup.sh --foreign-ip 203.0.113.10 --local-port 8080 --mode nginx

  # 3. Dry-run test without root privileges
  ./iran-relay-setup.sh --foreign-ip 203.0.113.10 --dry-run
EOF
}

# Parse environment variables if present
if [[ -n "${FOREIGN_VPS_IP:-}" ]]; then
    FOREIGN_IP="${FOREIGN_VPS_IP}"
fi
if [[ -n "${FOREIGN_VPS_PORT:-}" ]]; then
    FOREIGN_PORT="${FOREIGN_VPS_PORT}"
fi
if [[ -n "${LOCAL_RELAY_PORT:-}" ]]; then
    LOCAL_PORT="${LOCAL_RELAY_PORT}"
fi
if [[ -n "${RELAY_MODE:-}" ]]; then
    MODE="${RELAY_MODE}"
fi

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --foreign-ip)
            FOREIGN_IP="$2"
            shift 2
            ;;
        --foreign-port)
            FOREIGN_PORT="$2"
            shift 2
            ;;
        --local-port)
            LOCAL_PORT="$2"
            shift 2
            ;;
        --mode)
            MODE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --systemd-dir)
            SYSTEMD_DIR="$2"
            shift 2
            ;;
        --nginx-dir)
            NGINX_CONF_DIR="$2"
            shift 2
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            log_err "Unknown argument: $1"
            print_usage
            exit 1
            ;;
    esac
done

if [[ -z "${FOREIGN_IP}" ]]; then
    log_err "Missing required --foreign-ip argument or FOREIGN_VPS_IP environment variable."
    echo ""
    print_usage
    exit 1
fi

if [[ "${MODE}" != "socat" && "${MODE}" != "nginx" ]]; then
    log_err "Invalid mode: '${MODE}'. Supported modes: 'socat' or 'nginx'."
    exit 1
fi

check_root() {
    if [[ "${DRY_RUN}" = true ]]; then
        return 0
    fi
    if [[ $EUID -ne 0 ]]; then
        log_err "This script requires root privileges to install packages and system services. Please run with sudo."
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# Mode 1: socat Layer-4 TCP Forwarder via systemd
# ------------------------------------------------------------------------------
setup_socat_relay() {
    log_info "Configuring socat TCP relay (Mode: socat)"
    log_info "Forwarding local port ${LOCAL_PORT} -> ${FOREIGN_IP}:${FOREIGN_PORT}"

    SOCAT_BIN="$(command -v socat || true)"
    if [[ -z "${SOCAT_BIN}" ]]; then
        if [[ "${DRY_RUN}" = true ]]; then
            log_warn "[Dry-run] socat not installed; would run: apt-get update && apt-get install -y socat"
            SOCAT_BIN="/usr/bin/socat"
        else
            log_info "Installing socat package..."
            if command -v apt-get >/dev/null 2>&1; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -qq && apt-get install -y -qq socat
            elif command -v yum >/dev/null 2>&1; then
                yum install -y -q socat
            elif command -v apk >/dev/null 2>&1; then
                apk add --no-cache socat
            else
                log_err "Cannot find package manager to install socat. Please install socat manually."
                exit 1
            fi
            SOCAT_BIN="$(command -v socat)"
        fi
    fi

    local service_content="[Unit]
Description=Cluster Chat Domestic Iranian Relay (socat TCP Forwarder)
Documentation=https://github.com/leaddesk/agw-workers
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nobody
Group=nogroup
Restart=always
RestartSec=3s
ExecStart=${SOCAT_BIN} TCP4-LISTEN:${LOCAL_PORT},fork,reuseaddr TCP4:${FOREIGN_IP}:${FOREIGN_PORT}
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
"

    local service_file="${SYSTEMD_DIR}/${SERVICE_NAME}.service"

    if [[ "${DRY_RUN}" = true ]]; then
        log_info "[Dry-run] Generated systemd unit (${service_file}):"
        echo "--------------------------------------------------------"
        echo "${service_content}"
        echo "--------------------------------------------------------"
        log_info "[Dry-run] Would execute: systemctl daemon-reload && systemctl enable ${SERVICE_NAME} && systemctl restart ${SERVICE_NAME}"
        return 0
    fi

    mkdir -p "${SYSTEMD_DIR}"
    echo "${service_content}" > "${service_file}"
    chmod 644 "${service_file}"
    log_success "Created systemd service: ${service_file}"

    if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
        log_info "Reloading systemd and enabling service..."
        systemctl daemon-reload
        systemctl enable "${SERVICE_NAME}.service"
        systemctl restart "${SERVICE_NAME}.service"
        log_success "Service ${SERVICE_NAME} started and enabled on boot."
    else
        log_warn "systemd not detected or running in container. Start command manually:"
        echo "  ${SOCAT_BIN} TCP4-LISTEN:${LOCAL_PORT},fork,reuseaddr TCP4:${FOREIGN_IP}:${FOREIGN_PORT} &"
    fi
}

# ------------------------------------------------------------------------------
# Mode 2: Nginx Layer-7 Reverse Proxy
# ------------------------------------------------------------------------------
setup_nginx_relay() {
    log_info "Configuring Nginx Layer-7 reverse proxy (Mode: nginx)"
    log_info "Proxying local port ${LOCAL_PORT} -> ${FOREIGN_IP}:${FOREIGN_PORT}"

    NGINX_BIN="$(command -v nginx || true)"
    if [[ -z "${NGINX_BIN}" ]]; then
        if [[ "${DRY_RUN}" = true ]]; then
            log_warn "[Dry-run] nginx not installed; would run: apt-get update && apt-get install -y nginx"
            NGINX_BIN="/usr/sbin/nginx"
        else
            log_info "Installing nginx package..."
            if command -v apt-get >/dev/null 2>&1; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -qq && apt-get install -y -qq nginx
            elif command -v yum >/dev/null 2>&1; then
                yum install -y -q nginx
            elif command -v apk >/dev/null 2>&1; then
                apk add --no-cache nginx
            else
                log_err "Cannot find package manager to install nginx. Please install nginx manually."
                exit 1
            fi
            NGINX_BIN="$(command -v nginx)"
        fi
    fi

    local template_file="${SCRIPT_DIR}/nginx-iran-relay.conf"
    local target_conf="${NGINX_CONF_DIR}/cluster-chat-relay.conf"

    local nginx_content
    if [[ -f "${template_file}" ]]; then
        nginx_content="$(sed \
            -e "s/203.0.113.10:8080/${FOREIGN_IP}:${FOREIGN_PORT}/g" \
            -e "s/listen 8080;/listen ${LOCAL_PORT};/g" \
            -e "s/listen \[::\]:8080;/listen [::]:${LOCAL_PORT};/g" \
            "${template_file}")"
    else
        nginx_content="map \$http_upgrade \$connection_upgrade {
    default upgrade;
    ''      close;
}

upstream foreign_cluster_chat_vps {
    server ${FOREIGN_IP}:${FOREIGN_PORT};
    keepalive 32;
}

server {
    listen ${LOCAL_PORT};
    listen [::]:${LOCAL_PORT};
    server_name _;
    client_max_body_size 50M;

    proxy_http_version 1.1;
    proxy_set_header Host \$http_host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Host \$http_host;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;

    location / {
        proxy_pass http://foreign_cluster_chat_vps;
        proxy_redirect off;
    }
}
"
    fi

    if [[ "${DRY_RUN}" = true ]]; then
        log_info "[Dry-run] Generated Nginx configuration (${target_conf}):"
        echo "--------------------------------------------------------"
        echo "${nginx_content}"
        echo "--------------------------------------------------------"
        log_info "[Dry-run] Would execute: nginx -t && systemctl reload nginx"
        return 0
    fi

    mkdir -p "${NGINX_CONF_DIR}"
    echo "${nginx_content}" > "${target_conf}"
    chmod 644 "${target_conf}"
    log_success "Created Nginx config: ${target_conf}"

    log_info "Testing Nginx configuration syntax..."
    nginx -t
    log_success "Nginx syntax test passed."

    if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
        systemctl reload nginx || systemctl restart nginx
        systemctl enable nginx
        log_success "Nginx reloaded and active."
    else
        nginx -s reload || nginx
        log_success "Nginx reloaded."
    fi
}

main() {
    check_root
    log_info "Starting Cluster Chat Domestic Relay Setup"
    log_info "Foreign VPS Target: ${FOREIGN_IP}:${FOREIGN_PORT}"
    log_info "Local Relay Port:   ${LOCAL_PORT}"
    log_info "Forwarding Mode:    ${MODE}"

    if [[ "${MODE}" == "socat" ]]; then
        setup_socat_relay
    elif [[ "${MODE}" == "nginx" ]]; then
        setup_nginx_relay
    fi

    echo ""
    log_success "=========================================================="
    log_success " Domestic Relay Setup Complete!"
    log_success "=========================================================="
    log_info "Test connection locally on this Iranian relay server:"
    echo "  curl -i http://127.0.0.1:${LOCAL_PORT}/api/health"
    echo ""
    log_info "Clients in Iran can now connect to Cluster Chat at:"
    echo "  http://<THIS_RELAY_IP>:${LOCAL_PORT}/"
    log_success "=========================================================="
}

main "$@"
