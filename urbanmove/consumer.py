"""
UrbanMove — Stream Processor / Kafka Consumer
=============================================
Runs on the private-subnet EC2 (urbanmove-stream-processor).
Consumes:
  • urbanmove.gps.events    → persists to PostgreSQL gps_events table
  • urbanmove.congestion    → updates Redis congestion cache
  • urbanmove.alerts        → forwards critical alerts to SNS

Usage:
    python3 consumer.py

Environment variables (loaded from .env):
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
    REDIS_HOST, REDIS_PORT
    KAFKA_BROKERS
    SNS_TOPIC_ARN, AWS_REGION
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import boto3
import psycopg2
import psycopg2.extras
import redis
from dotenv import load_dotenv
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

# ── Environment ──────────────────────────────────────────────────────────────
load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
log = logging.getLogger("urbanmove.consumer")

# ── Topics ───────────────────────────────────────────────────────────────────
TOPICS = [
    "urbanmove.gps.events",
    "urbanmove.congestion",
    "urbanmove.alerts",
]

# ── How many GPS rows to batch before a single INSERT ────────────────────────
GPS_BATCH_SIZE = 50


# ── DB connection ─────────────────────────────────────────────────────────────
def get_db() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "urbanmove"),
        user=os.getenv("DB_USER", "admin"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=10,
    )


# ── Redis connection ──────────────────────────────────────────────────────────
def get_redis() -> redis.Redis:
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True,
        socket_connect_timeout=5,
    )


# ── SNS client ────────────────────────────────────────────────────────────────
def get_sns():
    return boto3.client("sns", region_name=os.getenv("AWS_REGION", "us-east-1"))


# ── GPS batch flush → PostgreSQL ──────────────────────────────────────────────
def flush_gps_batch(batch: list[dict], conn: psycopg2.extensions.connection) -> int:
    """Insert a batch of GPS events into gps_events table. Returns rows inserted."""
    if not batch:
        return 0
    sql = """
        INSERT INTO gps_events (vehicle_id, lat, lon, speed_kmh, heading, passengers, recorded_at)
        VALUES %s
        ON CONFLICT DO NOTHING
    """
    rows = [
        (
            e.get("vehicle_id"),
            e.get("lat"),
            e.get("lon"),
            e.get("speed_kmh", 0),
            e.get("heading"),
            e.get("passengers", 0),
            e.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )
        for e in batch
    ]
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, rows)
        conn.commit()
        return len(rows)
    except Exception as exc:
        conn.rollback()
        log.error("GPS batch insert failed: %s", exc)
        return 0


# ── Congestion → Redis ────────────────────────────────────────────────────────
def handle_congestion(payload: dict, r: redis.Redis) -> None:
    """Cache congestion zone level in Redis with 60-second TTL."""
    zone = payload.get("zone_name", "unknown")
    level = payload.get("level", "low")
    key = f"urbanmove:congestion:{zone}"
    try:
        r.setex(key, 60, json.dumps({"zone_name": zone, "level": level,
                                      "updated_at": datetime.now(timezone.utc).isoformat()}))
        log.debug("Congestion cached: %s → %s", zone, level)
    except Exception as exc:
        log.warning("Redis write failed: %s", exc)


# ── Alerts → SNS ─────────────────────────────────────────────────────────────
def handle_alert(payload: dict, sns_client, topic_arn: str) -> None:
    """Forward critical alerts to SNS for email/SMS notification."""
    severity = payload.get("severity", "info")
    if severity != "critical":
        return  # only escalate critical alerts

    message = payload.get("message", "No message")
    vehicle_id = payload.get("vehicle_id", "N/A")
    zone = payload.get("zone_name", "N/A")

    subject = f"[UrbanMove CRITICAL] {payload.get('type', 'alert').upper()}"
    body = (
        f"Severity : {severity.upper()}\n"
        f"Type     : {payload.get('type', 'unknown')}\n"
        f"Message  : {message}\n"
        f"Vehicle  : {vehicle_id}\n"
        f"Zone     : {zone}\n"
        f"Time     : {datetime.now(timezone.utc).isoformat()}\n"
    )

    if not topic_arn or topic_arn.startswith("<"):
        log.warning("SNS_TOPIC_ARN not configured — skipping SNS publish")
        return

    try:
        sns_client.publish(TopicArn=topic_arn, Subject=subject, Message=body)
        log.info("📢 SNS alert published: %s", subject)
    except Exception as exc:
        log.error("SNS publish failed: %s", exc)


# ── Build Kafka consumer ──────────────────────────────────────────────────────
def build_consumer(brokers: str, retries: int = 10) -> KafkaConsumer:
    for attempt in range(1, retries + 1):
        try:
            consumer = KafkaConsumer(
                *TOPICS,
                bootstrap_servers=brokers.split(","),
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                auto_offset_reset="latest",
                enable_auto_commit=True,
                group_id="urbanmove-stream-processor",
                session_timeout_ms=30000,
                heartbeat_interval_ms=10000,
            )
            log.info("✅ Kafka consumer connected to %s", brokers)
            log.info("   Subscribed topics: %s", TOPICS)
            return consumer
        except NoBrokersAvailable:
            log.warning("Kafka not reachable (attempt %d/%d). Retrying in 10s…", attempt, retries)
            time.sleep(10)
    log.error("Could not connect to Kafka after %d attempts. Exiting.", retries)
    sys.exit(1)


# ── Main loop ─────────────────────────────────────────────────────────────────
def main() -> None:
    brokers   = os.getenv("KAFKA_BROKERS", "localhost:9092")
    topic_arn = os.getenv("SNS_TOPIC_ARN", "")

    log.info("🚀 UrbanMove Stream Processor starting")
    log.info("   Kafka brokers : %s", brokers)
    log.info("   Topics        : %s", TOPICS)
    log.info("   GPS batch size: %d", GPS_BATCH_SIZE)

    # Service clients
    db_conn   = get_db()
    r         = get_redis()
    sns       = get_sns()
    consumer  = build_consumer(brokers)

    # Counters
    counts = {"gps": 0, "congestion": 0, "alerts": 0, "errors": 0}
    gps_batch: list[dict] = []
    last_stats = time.monotonic()

    # Graceful shutdown
    running = True
    def _shutdown(sig, frame):
        nonlocal running
        log.info("Shutting down stream processor…")
        running = False
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("✅ Consuming messages… (Ctrl+C to stop)")

    for msg in consumer:
        if not running:
            break

        topic   = msg.topic
        payload = msg.value

        try:
            if topic == "urbanmove.gps.events":
                gps_batch.append(payload)
                if len(gps_batch) >= GPS_BATCH_SIZE:
                    inserted = flush_gps_batch(gps_batch, db_conn)
                    counts["gps"] += inserted
                    log.debug("GPS batch flushed: %d rows", inserted)
                    gps_batch.clear()

            elif topic == "urbanmove.congestion":
                handle_congestion(payload, r)
                counts["congestion"] += 1

            elif topic == "urbanmove.alerts":
                handle_alert(payload, sns, topic_arn)
                counts["alerts"] += 1

        except Exception as exc:
            counts["errors"] += 1
            log.error("Error processing message from %s: %s", topic, exc)
            # Reconnect DB if connection dropped
            try:
                db_conn.close()
            except Exception:
                pass
            try:
                db_conn = get_db()
            except Exception as e:
                log.error("DB reconnect failed: %s", e)

        # Print stats every 30 seconds
        now = time.monotonic()
        if now - last_stats >= 30:
            log.info(
                "📊 Stats → GPS: %d | Congestion: %d | Alerts: %d | Errors: %d",
                counts["gps"], counts["congestion"], counts["alerts"], counts["errors"],
            )
            last_stats = now

    # Flush remaining GPS batch on shutdown
    if gps_batch:
        flush_gps_batch(gps_batch, db_conn)

    consumer.close()
    db_conn.close()
    log.info("✅ Stream processor stopped. Final counts: %s", counts)


if __name__ == "__main__":
    main()
