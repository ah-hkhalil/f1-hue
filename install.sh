#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# F1 25 Hue — install script
# Sets up a systemd service so the script runs on boot.
#
# Usage:
#   bash install.sh
# ─────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="f1hue"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PYTHON=$(which python3)
USER_NAME=$(whoami)

echo ""
echo "════════════════════════════════════════"
echo "  F1 25 Hue — installer"
echo "════════════════════════════════════════"
echo ""

# ── 1. Check config has been filled in ───────────────────────────
if grep -q "YOUR_BRIDGE_IP\|YOUR_HUE_API_KEY\|YOUR_LIGHT_ID\|YOUR_GAMERTAG" "${SCRIPT_DIR}/f1_hue.py"; then
  echo "✗ Please edit f1_hue.py and fill in your config values before installing."
  echo "  See README.md for instructions."
  exit 1
fi

# ── 2. Install Python dependencies ───────────────────────────────
echo "▶ Installing Python dependencies..."
pip3 install requests --break-system-packages --quiet
echo "  ✓ requests installed"

# ── 3. Create systemd service ────────────────────────────────────
echo ""
echo "▶ Creating systemd service: ${SERVICE_NAME}"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=F1 25 Hue Flag Lights
After=network.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${PYTHON} ${SCRIPT_DIR}/f1_hue.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo "  ✓ Service installed and started"
echo "  ✓ Will auto-start on every boot"

# ── 4. Done ───────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "  ✓ All done!"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status ${SERVICE_NAME}   — check if running"
echo "    sudo systemctl stop ${SERVICE_NAME}     — stop the service"
echo "    sudo systemctl start ${SERVICE_NAME}    — start the service"
echo "    sudo journalctl -u ${SERVICE_NAME} -f   — live log output"
echo ""
echo "  Pi IP address: $(hostname -I | awk '{print $1}')"
echo "  (use this as the telemetry IP in F1 25 settings)"
echo "════════════════════════════════════════"
echo ""
