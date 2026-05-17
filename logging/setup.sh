#!/usr/bin/env bash
# Kotamech Logger - Raspberry Pi bootstrap.
# Edit the three placeholders below, then run with sudo from this directory:
#   sudo ./setup.sh

set -euo pipefail

# --- Configuration ---------------------------------------------------------
TS_AUTHKEY="tskey-auth-FILL-ME-IN"
TS_TAG="tag:device"
TS_HOSTNAME="FILL-ME-IN"
# -------------------------------------------------------------------------

if [[ "$EUID" -ne 0 ]]; then
    echo "setup.sh must be run as root (try: sudo ./setup.sh)" >&2
    exit 1
fi

for var in TS_AUTHKEY TS_TAG TS_HOSTNAME; do
    if [[ "${!var}" == *FILL-ME-IN* ]]; then
        echo "Error: $var is still a placeholder. Edit setup.sh." >&2
        exit 1
    fi
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/6] apt update + install python3, python3-requests, curl"
apt-get update
apt-get install -y python3 python3-requests curl

echo "[2/6] Installing Tailscale"
curl -fsSL https://tailscale.com/install.sh | sh

echo "[3/6] Bringing up Tailscale (hostname=$TS_HOSTNAME, tag=$TS_TAG)"
tailscale up \
    --authkey="$TS_AUTHKEY" \
    --advertise-tags="$TS_TAG" \
    --hostname="$TS_HOSTNAME"

echo "[4/6] Installing client.py to /opt/kotamech-logger/"
install -m 755 -D "$SCRIPT_DIR/client.py" /opt/kotamech-logger/client.py

echo "[5/6] Installing systemd units"
install -m 644 "$SCRIPT_DIR/kotamech-logger.service" /etc/systemd/system/kotamech-logger.service
install -m 644 "$SCRIPT_DIR/kotamech-logger.timer"   /etc/systemd/system/kotamech-logger.timer
systemctl daemon-reload

echo "[6/6] Enabling kotamech-logger.timer"
systemctl enable --now kotamech-logger.timer

echo
echo "Done."
echo "  Tailscale IP:  $(tailscale ip -4 2>/dev/null || echo '(unavailable)')"
echo "  Timer status:  $(systemctl is-active kotamech-logger.timer)"
echo "  Logs:          journalctl -u kotamech-logger.service -f"
echo "  Trigger now:   sudo systemctl start kotamech-logger.service"
