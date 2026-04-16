#!/bin/bash
# ════════════════════════════════════════════════════════════════════
# UrbanMove — Stream Processor EC2 Bootstrap Script
# Run as: bash setup_processor.sh
# Must be executed from /home/ec2-user/urbanmove/
# This runs on the PRIVATE SUBNET EC2 (urbanmove-stream-processor)
# ════════════════════════════════════════════════════════════════════

set -euo pipefail

APP_DIR="/home/ec2-user/urbanmove"
SERVICE_USER="ec2-user"
LOG_DIR="$APP_DIR/logs"
SERVICE_FILE="/etc/systemd/system/urbanmove-processor.service"

echo "========================================"
echo "  UrbanMove Stream Processor Setup"
echo "========================================"

# ── 1. System update ─────────────────────────────────────────────────
echo "[1/6] Updating system packages..."
sudo yum update -y -q

# ── 2. Install Python 3.11 + pip ─────────────────────────────────────
echo "[2/6] Installing Python 3.11 and pip..."
sudo yum install -y python3.11 python3.11-pip -q

sudo alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 2>/dev/null || true
sudo alternatives --install /usr/bin/pip3 pip3 /usr/bin/pip3.11 1 2>/dev/null || true

python3 --version
pip3 --version

# ── 3. Install Python dependencies ────────────────────────────────────
echo "[3/6] Installing Python dependencies..."
cd "$APP_DIR"
pip3 install --upgrade pip -q
pip3 install \
    kafka-python==2.0.2 \
    psycopg2-binary==2.9.9 \
    redis==5.0.7 \
    python-dotenv==1.0.1 \
    boto3==1.34.149 \
    -q

echo "  kafka-python, psycopg2, redis, dotenv, boto3 — installed"

# ── 4. Validate .env ──────────────────────────────────────────────────
echo "[4/6] Checking .env file..."
if [ ! -f "$APP_DIR/.env" ]; then
    echo "ERROR: .env not found at $APP_DIR/.env"
    echo "       Upload .env with real values before running this script."
    exit 1
fi

if grep -q "<" "$APP_DIR/.env"; then
    echo ""
    echo "WARNING: .env still has unfilled placeholders (<...>)."
    echo "         KAFKA_BROKERS and DB_HOST must be set for consumer.py to work."
    echo ""
fi

# ── 5. Create log directory ───────────────────────────────────────────
echo "[5/6] Creating log directory..."
mkdir -p "$LOG_DIR"
chown "$SERVICE_USER":"$SERVICE_USER" "$LOG_DIR"

# ── 6. Create systemd service for the Kafka consumer ─────────────────
echo "[6/6] Registering urbanmove-processor systemd service..."
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=UrbanMove Kafka Stream Processor
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=/usr/bin/python3 $APP_DIR/consumer.py
Restart=always
RestartSec=10
StandardOutput=append:$LOG_DIR/processor.log
StandardError=append:$LOG_DIR/processor-error.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable urbanmove-processor
sudo systemctl restart urbanmove-processor

sleep 3

STATUS=$(sudo systemctl is-active urbanmove-processor || true)
echo ""
if [ "$STATUS" = "active" ]; then
    echo "========================================"
    echo "  Stream Processor is RUNNING!"
    echo "  Consuming: urbanmove.gps.events"
    echo "             urbanmove.congestion"
    echo "             urbanmove.alerts"
    echo "========================================"
else
    echo "========================================"
    echo "  WARNING: Service status = $STATUS"
    echo "  Check: sudo journalctl -u urbanmove-processor -n 50"
    echo "      or: tail -f $LOG_DIR/processor-error.log"
    echo "========================================"
fi

echo ""
echo "Useful commands:"
echo "  sudo systemctl status urbanmove-processor"
echo "  sudo journalctl -u urbanmove-processor -f"
echo "  tail -f $LOG_DIR/processor.log"
echo ""
echo "To run manually instead of as a service:"
echo "  python3 $APP_DIR/consumer.py"
