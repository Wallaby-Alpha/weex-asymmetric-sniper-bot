#!/usr/bin/env bash
# ==============================================================================
# setup_droplet.sh
# ------------------------------------------------------------------------------
# Automated deployment script for WEEX 4h Asymmetric Sniper Bot on Ubuntu/Debian.
# ==============================================================================

set -e

echo "=================================================================="
echo "🚀 Installing WEEX 4h Asymmetric Sniper Trading Bot..."
echo "=================================================================="

# 1. Update OS and install prerequisites
apt-get update -y
apt-get install -y python3 python3-pip python3-venv git curl

BOT_DIR="/root/weex-asymmetric-sniper-bot"
cd "$BOT_DIR"

# 2. Setup Python virtual environment
echo "📦 Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Create config.json if not present
if [ ! -f "config.json" ]; then
    echo "⚠️  config.json not found. Copying config.example.json..."
    cp config.example.json config.json
    echo "❗ IMPORTANT: Edit config.json with your WEEX API keys and Telegram credentials before running live."
fi

# 4. Install systemd service
SERVICE_PATH="/etc/systemd/system/weex-sniper.service"
echo "⚙️  Configuring systemd service ($SERVICE_PATH)..."

cat <<EOF > "$SERVICE_PATH"
[Unit]
Description=WEEX 4-Hour Asymmetric Sniper Bot Daemon
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python3 $BOT_DIR/bot.py
Restart=always
RestartSec=10
StandardOutput=append:$BOT_DIR/weex_sniper.log
StandardError=append:$BOT_DIR/weex_sniper.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable weex-sniper.service

echo "=================================================================="
echo "✅ Installation Complete!"
echo "=================================================================="
echo "Next steps:"
echo "1. Edit configuration:"
echo "   nano $BOT_DIR/config.json"
echo "2. Start the bot daemon:"
echo "   systemctl start weex-sniper"
echo "3. Check live logs:"
echo "   journalctl -u weex-sniper -f"
echo "   OR"
echo "   tail -f $BOT_DIR/weex_sniper.log"
echo "4. Check bot status:"
echo "   systemctl status weex-sniper"
echo "=================================================================="
