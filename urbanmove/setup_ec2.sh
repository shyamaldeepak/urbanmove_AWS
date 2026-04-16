#!/bin/bash
# ════════════════════════════════════════════════════════════════════
# UrbanMove — EC2 Bootstrap Setup Script
# Run as: bash setup_ec2.sh
# Must be executed from /home/ec2-user/urbanmove/
# ════════════════════════════════════════════════════════════════════

set -euo pipefail

APP_DIR="/home/ec2-user/urbanmove"
SERVICE_USER="ec2-user"
LOG_DIR="$APP_DIR/logs"
SERVICE_FILE="/etc/systemd/system/urbanmove.service"

echo "========================================"
echo "  UrbanMove EC2 Setup — Starting..."
echo "========================================"

# ── 1. System update ────────────────────────────────────────────────
echo "[1/8] Updating system packages..."
sudo yum update -y -q

# ── 2. Install Python 3.11 + pip + PostgreSQL client ────────────────
echo "[2/8] Installing Python 3.11, pip, and PostgreSQL client..."
sudo yum install -y python3.11 python3.11-pip postgresql15 -q

# Symlink so 'python3' and 'pip3' resolve correctly
sudo alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 2>/dev/null || true
sudo alternatives --install /usr/bin/pip3 pip3 /usr/bin/pip3.11 1 2>/dev/null || true

python3 --version
pip3 --version

# ── 3. Install Python dependencies ──────────────────────────────────
echo "[3/8] Installing Python dependencies from requirements.txt..."
cd "$APP_DIR"
pip3 install --upgrade pip -q
pip3 install -r requirements.txt -q

# ── 4. Validate .env exists and has real values ──────────────────────
echo "[4/8] Validating .env file..."
if [ ! -f "$APP_DIR/.env" ]; then
    echo "ERROR: .env file not found at $APP_DIR/.env"
    echo "       Copy .env, fill in all values, then re-run this script."
    exit 1
fi

# Warn if placeholders still exist
if grep -q "<" "$APP_DIR/.env"; then
    echo ""
    echo "WARNING: .env still contains unfilled placeholders (<...>)."
    echo "         Edit .env with real AWS values before starting the app."
    echo "         Continuing setup, but the app may fail to connect to services."
    echo ""
fi

# ── 5. Create log directory ──────────────────────────────────────────
echo "[5/8] Creating log directory..."
mkdir -p "$LOG_DIR"
chown "$SERVICE_USER":"$SERVICE_USER" "$LOG_DIR"

# ── 6. Install CloudWatch Agent ──────────────────────────────────────
echo "[6/8] Installing Amazon CloudWatch Agent..."
sudo yum install -y amazon-cloudwatch-agent -q

# Write CloudWatch config if not already present
CW_CONFIG="/opt/aws/amazon-cloudwatch-agent/etc/urbanmove-cw-config.json"
if [ ! -f "$CW_CONFIG" ]; then
    sudo tee "$CW_CONFIG" > /dev/null <<EOF
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "$LOG_DIR/app.log",
            "log_group_name": "/urbanmove/app",
            "log_stream_name": "{instance_id}/app",
            "timezone": "UTC"
          },
          {
            "file_path": "$LOG_DIR/error.log",
            "log_group_name": "/urbanmove/app",
            "log_stream_name": "{instance_id}/error",
            "timezone": "UTC"
          },
          {
            "file_path": "/var/log/messages",
            "log_group_name": "/urbanmove/app",
            "log_stream_name": "{instance_id}/messages",
            "timezone": "UTC"
          }
        ]
      }
    }
  }
}
EOF
fi

sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config -m ec2 -c "file:$CW_CONFIG" -s || \
    echo "  (CloudWatch Agent config reload skipped — may need IAM role attached)"

# ── 7. Create systemd service ────────────────────────────────────────
echo "[7/8] Creating systemd service: urbanmove..."
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=UrbanMove Smart Mobility Platform
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=/usr/bin/python3 -m gunicorn app:app \
    --bind 0.0.0.0:5000 \
    --workers 2 \
    --timeout 60 \
    --access-logfile $LOG_DIR/app.log \
    --error-logfile $LOG_DIR/error.log \
    --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable urbanmove
sudo systemctl restart urbanmove

# ── 8. Health check ──────────────────────────────────────────────────
echo "[8/8] Running health check..."
sleep 4

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/health || echo "000")

if [ "$HTTP_STATUS" = "200" ]; then
    HEALTH_BODY=$(curl -s http://localhost:5000/health)
    echo ""
    echo "========================================"
    echo "  UrbanMove is UP and HEALTHY!"
    echo "  /health → $HEALTH_BODY"
    echo "========================================"
else
    echo ""
    echo "========================================"
    echo "  WARNING: /health returned HTTP $HTTP_STATUS"
    echo "  Check logs: sudo journalctl -u urbanmove -n 50"
    echo "           or: tail -f $LOG_DIR/error.log"
    echo "========================================"
fi

echo ""
echo "Setup complete. Useful commands:"
echo "  sudo systemctl status urbanmove"
echo "  sudo journalctl -u urbanmove -f"
echo "  tail -f $LOG_DIR/app.log"
