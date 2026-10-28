#!/usr/bin/env bash
# ==============================================================================
# LeadDesk AI - Cluster Chat GitHub Relay Installer
# Deploys /opt/hq/github_relay.py, relay_protocol.py, environment template,
# and optional systemd service unit on the LeadDesk VPS.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

TARGET_DIR="/opt/hq"
SERVICE_DIR="/etc/systemd/system"
DRY_RUN=false
INSTALL_SERVICE=true

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --dry-run               Simulate installation steps without modifying files
  --target-dir <path>     Target installation directory (default: /opt/hq)
  --service-dir <path>    Systemd unit directory (default: /etc/systemd/system)
  --no-service            Skip installing the systemd service unit
  -h, --help              Display this help message
EOF
    exit 0
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --target-dir)
            TARGET_DIR="$2"
            shift 2
            ;;
        --target-dir=*)
            TARGET_DIR="${1#*=}"
            shift
            ;;
        --service-dir)
            SERVICE_DIR="$2"
            shift 2
            ;;
        --service-dir=*)
            SERVICE_DIR="${1#*=}"
            shift
            ;;
        --no-service)
            INSTALL_SERVICE=false
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            ;;
    esac
done

echo "====================================================================="
echo " LeadDesk Cluster Chat - GitHub Relay Installer"
echo " Target Directory:   ${TARGET_DIR}"
echo " Service Directory:  ${SERVICE_DIR}"
echo " Dry Run Mode:       ${DRY_RUN}"
echo "====================================================================="

# Verification of required source files
SRC_RELAY_PY="${SCRIPT_DIR}/github_relay.py"
SRC_PROTOCOL_PY="${REPO_ROOT}/cluster_chat/relay_protocol.py"
SRC_SERVICE="${SCRIPT_DIR}/systemd/leaddesk-github-relay.service"
SRC_ENV="${SCRIPT_DIR}/github_relay.env.example"

for src in "${SRC_RELAY_PY}" "${SRC_PROTOCOL_PY}" "${SRC_SERVICE}" "${SRC_ENV}"; do
    if [[ ! -f "${src}" ]]; then
        echo "Error: Required source file not found: ${src}" >&2
        exit 1
    fi
done

run_cmd() {
    if [[ "${DRY_RUN}" == "true" ]]; then
        echo "[DRY-RUN] $*"
    else
        echo "[EXEC] $*"
        "$@"
    fi
}

# 1. Ensure target directory structure exists
echo ">>> Setting up target directories..."
run_cmd mkdir -p "${TARGET_DIR}"
run_cmd mkdir -p "${TARGET_DIR}/data"
run_cmd mkdir -p "${TARGET_DIR}/data/cluster-chat/uploads"

# 2. Install Python scripts into target directory
echo ">>> Installing daemon script and protocol library..."
run_cmd cp "${SRC_RELAY_PY}" "${TARGET_DIR}/github_relay.py"
run_cmd chmod 755 "${TARGET_DIR}/github_relay.py"

run_cmd cp "${SRC_PROTOCOL_PY}" "${TARGET_DIR}/relay_protocol.py"
run_cmd chmod 644 "${TARGET_DIR}/relay_protocol.py"

# 3. Setup environment configuration
ENV_TARGET="${TARGET_DIR}/github_relay.env"
if [[ -f "${ENV_TARGET}" ]]; then
    echo ">>> Configuration file already exists at ${ENV_TARGET}. Skipping overwrite."
else
    echo ">>> Installing environment template to ${ENV_TARGET}..."
    run_cmd cp "${SRC_ENV}" "${ENV_TARGET}"
    run_cmd chmod 600 "${ENV_TARGET}"
fi

# 4. Install systemd service if requested and permitted
if [[ "${INSTALL_SERVICE}" == "true" ]]; then
    if [[ -d "${SERVICE_DIR}" ]]; then
        echo ">>> Installing systemd service unit..."
        SERVICE_TARGET="${SERVICE_DIR}/leaddesk-github-relay.service"
        run_cmd cp "${SRC_SERVICE}" "${SERVICE_TARGET}"
        run_cmd chmod 644 "${SERVICE_TARGET}"
        if command -v systemctl >/dev/null 2>&1 && [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
            echo ">>> Reloading systemd daemon..."
            run_cmd systemctl daemon-reload
        else
            echo "Note: Run 'sudo systemctl daemon-reload' to register the new unit."
        fi
    else
        echo "Warning: Systemd service directory ${SERVICE_DIR} not found. Skipping service unit installation."
    fi
fi

echo "====================================================================="
echo " Installation Complete!"
echo " Daemon executable:  ${TARGET_DIR}/github_relay.py"
echo " Shared protocol:    ${TARGET_DIR}/relay_protocol.py"
echo " Config file:        ${TARGET_DIR}/github_relay.env"
echo ""
echo " Next steps:"
echo " 1. Edit ${TARGET_DIR}/github_relay.env to set your GITHUB_TOKEN and passphrases."
echo " 2. Test status:"
echo "    python3 ${TARGET_DIR}/github_relay.py --status"
echo " 3. Start service (systemd):"
echo "    sudo systemctl enable --now leaddesk-github-relay"
echo "====================================================================="
