#!/bin/bash
# Installs arcade-control as a systemd service that starts on boot.
# Must be run as root (sudo ./install.sh)

set -e

SERVICE_NAME="arcade-control"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_SRC="$SCRIPT_DIR/systemd/${SERVICE_NAME}.service"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}.service"

# ── Check root ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run this script as root: sudo $0"
    exit 1
fi

# ── Copy service file ─────────────────────────────────────────────────────────
echo "[1/4] Copying service file to $SERVICE_DST"
cp "$SERVICE_SRC" "$SERVICE_DST"

# ── Reload systemd ────────────────────────────────────────────────────────────
echo "[2/4] Reloading systemd"
systemctl daemon-reload

# ── Enable service ────────────────────────────────────────────────────────────
echo "[3/4] Enabling $SERVICE_NAME (autostart on boot)"
systemctl enable "$SERVICE_NAME"

# ── Start service now ─────────────────────────────────────────────────────────
echo "[4/4] Starting $SERVICE_NAME now"
systemctl start "$SERVICE_NAME"

echo ""
echo "Done. Use these commands to manage the service:"
echo "  Status : sudo systemctl status $SERVICE_NAME"
echo "  Logs   : sudo journalctl -u $SERVICE_NAME -f"
echo "  Stop   : sudo systemctl stop $SERVICE_NAME"
echo "  Disable: sudo systemctl disable $SERVICE_NAME"
