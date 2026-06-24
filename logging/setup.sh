#!/usr/bin/env bash
# Kotamech Logger - Raspberry Pi bootstrap.
# Edit the three placeholders below, then run with sudo from this directory:
#   sudo ./setup.sh
#
# A venv is created at ./venv and the systemd service is pointed at it.

set -euo pipefail

# --- Configuration ---------------------------------------------------------
TS_AUTHKEY="tskey-auth-FILL-ME-IN"
TS_TAG="tag:device"
TS_HOSTNAME="FILL-ME-IN"
LOG_FILE="FILL-ME-IN"   # absolute path to the JSON log file Logger.py writes
# -------------------------------------------------------------------------

if [[ "$EUID" -ne 0 ]]; then
    echo "setup.sh must be run as root (try: sudo ./setup.sh)" >&2
    exit 1
fi

for var in TS_AUTHKEY TS_TAG TS_HOSTNAME LOG_FILE; do
    if [[ "${!var}" == *FILL-ME-IN* ]]; then
        echo "Error: $var is still a placeholder. Edit setup.sh." >&2
        exit 1
    fi
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/6] apt update + install python3, python3-venv, curl"
apt-get update
apt-get install -y python3 python3-venv curl

echo "[2/6] Installing Tailscale"
curl -fsSL https://tailscale.com/install.sh | sh

echo "[3/6] Bringing up Tailscale (hostname=$TS_HOSTNAME, tag=$TS_TAG)"
tailscale up \
    --authkey="$TS_AUTHKEY" \
    --advertise-tags="$TS_TAG" \
    --hostname="$TS_HOSTNAME"

echo "[4/6] Creating venv at $SCRIPT_DIR/venv and installing dependencies"
python3 -m venv "$SCRIPT_DIR/venv"
"$SCRIPT_DIR/venv/bin/pip" install --upgrade pip
"$SCRIPT_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

echo "[5/6] Installing systemd units (install dir=$SCRIPT_DIR)"
sed -e "s|__INSTALL_DIR__|${SCRIPT_DIR}|g" \
    -e "s|__LOG_FILE__|${LOG_FILE}|g" \
    "$SCRIPT_DIR/kotamech-logger.service" \
    > /etc/systemd/system/kotamech-logger.service
chmod 644 /etc/systemd/system/kotamech-logger.service
install -m 644 "$SCRIPT_DIR/kotamech-logger.timer" /etc/systemd/system/kotamech-logger.timer
systemctl daemon-reload

echo "[6/6] Enabling kotamech-logger.timer"
systemctl enable --now kotamech-logger.timer

echo
echo "Done."
echo "  Install dir:   $SCRIPT_DIR"
echo "  Tailscale IP:  $(tailscale ip -4 2>/dev/null || echo '(unavailable)')"
echo "  Timer status:  $(systemctl is-active kotamech-logger.timer)"
echo "  Logs:          journalctl -u kotamech-logger.service -f"
echo "  Trigger now:   sudo systemctl start kotamech-logger.service"
