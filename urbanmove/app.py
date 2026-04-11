import os
import json
import time
import threading
import logging
from datetime import datetime

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import psycopg2
import psycopg2.extras
import redis
from kafka import KafkaConsumer, KafkaProducer
from dotenv import load_dotenv
import boto3

# ── Load environment ───────────────────────────────────────────────────────────
load_dotenv()

# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── In-memory vehicle state (updated by Kafka consumer thread) ─────────────────
vehicle_state: dict[str, dict] = {}
alert_log: list[dict] = []

# ── DB helpers ─────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "urbanmove"),
        user=os.getenv("DB_USER", "admin"),
        password=os.getenv("DB_PASS", "UrbanDB!Secure2025"),
    )

def query(sql, params=None):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()

# ── Redis helper ───────────────────────────────────────────────────────────────
def get_redis():
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True,
    )

# ── Kafka consumer thread ──────────────────────────────────────────────────────
def start_kafka_consumer():
    brokers = os.getenv("KAFKA_BROKERS", "localhost:9092")
    topics = [
        "urbanmove.gps.events",
        "urbanmove.alerts",
        "urbanmove.congestion",
    ]
    try:
        consumer = KafkaConsumer(
            *topics,
            bootstrap_servers=brokers.split(","),
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="latest",
            group_id="urbanmove-app",
        )
        log.info("Kafka consumer connected to %s", brokers)
        for msg in consumer:
            payload = msg.value
            topic = msg.topic

            if topic == "urbanmove.gps.events":
                vid = payload.get("vehicle_id")
                if vid:
                    vehicle_state[vid] = {
                        **payload,
                        "last_seen": datetime.utcnow().isoformat(),
                    }

            elif topic == "urbanmove.alerts":
                alert_log.insert(0, {**payload, "ts": datetime.utcnow().isoformat()})
                if len(alert_log) > 100:
                    alert_log.pop()

    except Exception as e:
        log.warning("Kafka unavailable (%s). Running without live stream.", e)

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "region": os.getenv("AWS_REGION", "us-east-1")})


# ── Vehicles ───────────────────────────────────────────────────────────────────
@app.route("/api/vehicles")
def get_vehicles():
    """Returns current positions of all tracked vehicles."""
    # Add mock data so the map works even without Kafka
    if not vehicle_state:
        vehicles = _mock_vehicles()
    else:
        vehicles = list(vehicle_state.values())
    return jsonify(vehicles)


@app.route("/api/vehicles/<vehicle_id>")
def get_vehicle(vehicle_id):
    v = vehicle_state.get(vehicle_id)
    if not v:
        return jsonify({"error": "not found"}), 404
    return jsonify(v)


# ── Routes (transit) ────────────────────────────────────────────────────────────
@app.route("/api/routes")
def get_routes():
    try:
        rows = query("SELECT * FROM routes ORDER BY route_name")
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        log.warning("DB unavailable: %s", e)
        return jsonify(_mock_routes())


@app.route("/api/routes/<int:route_id>")
def get_route(route_id):
    try:
        rows = query("SELECT * FROM routes WHERE id = %s", (route_id,))
        if not rows:
            return jsonify({"error": "not found"}), 404
        return jsonify(dict(rows[0]))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Congestion ─────────────────────────────────────────────────────────────────
@app.route("/api/congestion")
def get_congestion():
    try:
        rows = query(
            "SELECT zone_name, level, updated_at FROM congestion_zones ORDER BY level DESC"
        )
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        log.warning("DB unavailable: %s", e)
        return jsonify(_mock_congestion())


# ── Alerts ─────────────────────────────────────────────────────────────────────
@app.route("/api/alerts")
def get_alerts():
    try:
        rows = query(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 20"
        )
        db_alerts = [dict(r) for r in rows]
    except Exception:
        db_alerts = []

    merged = (alert_log + db_alerts)[:20]
    return jsonify(merged)


# ── Stats ──────────────────────────────────────────────────────────────────────
@app.route("/api/stats")
def get_stats():
    active_vehicles = len(vehicle_state) if vehicle_state else 47
    try:
        r = get_redis()
        cached = r.get("urbanmove:stats")
        if cached:
            return jsonify(json.loads(cached))
    except Exception:
        pass

    try:
        rows = query("SELECT COUNT(*) AS cnt FROM routes WHERE active = TRUE")
        active_routes = rows[0]["cnt"]
    except Exception:
        active_routes = 12

    stats = {
        "active_vehicles": active_vehicles,
        "active_routes": active_routes,
        "avg_speed_kmh": 34.7,
        "congestion_zones": 3,
        "region": os.getenv("AWS_REGION", "us-east-1"),
        "timestamp": datetime.utcnow().isoformat(),
    }

    try:
        r = get_redis()
        r.setex("urbanmove:stats", 10, json.dumps(stats))
    except Exception:
        pass

    return jsonify(stats)


# ── Publish GPS event (for testing) ────────────────────────────────────────────
@app.route("/api/publish", methods=["POST"])
def publish_event():
    data = request.get_json()
    brokers = os.getenv("KAFKA_BROKERS", "localhost:9092")
    try:
        producer = KafkaProducer(
            bootstrap_servers=brokers.split(","),
            value_serializer=lambda v: json.dumps(v).encode(),
        )
        producer.send("urbanmove.gps.events", value=data)
        producer.flush()
        return jsonify({"status": "published"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Mock data (fallback when DB / Kafka not ready) ─────────────────────────────
def _mock_vehicles():
    import random, math
    base_lat, base_lon = 48.8566, 2.3522  # Paris
    vehicles = []
    types = ["Bus", "Tram", "Metro", "Shuttle"]
    statuses = ["moving", "moving", "moving", "stopped", "delayed"]
    for i in range(1, 26):
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(0.01, 0.08)
        speed = random.uniform(0, 60) if random.random() > 0.2 else 0
        vehicles.append({
            "vehicle_id": f"VH-{i:03}",
            "type": random.choice(types),
            "lat": base_lat + dist * math.cos(angle),
            "lon": base_lon + dist * math.sin(angle),
            "speed_kmh": round(speed, 1),
            "heading": round(random.uniform(0, 360), 1),
            "status": statuses[i % len(statuses)],
            "route_id": random.randint(1, 12),
            "passengers": random.randint(0, 80),
            "last_seen": datetime.utcnow().isoformat(),
        })
    return vehicles


def _mock_routes():
    return [
        {"id": i, "route_name": f"Line {['A','B','C','D','E','Metro 1','Metro 2','Tram 3'][i % 8]}",
         "active": True, "vehicle_count": i + 2}
        for i in range(1, 13)
    ]


def _mock_congestion():
    return [
        {"zone_name": "Châtelet–Les Halles", "level": "high", "updated_at": datetime.utcnow().isoformat()},
        {"zone_name": "La Défense", "level": "medium", "updated_at": datetime.utcnow().isoformat()},
        {"zone_name": "Gare du Nord", "level": "low", "updated_at": datetime.utcnow().isoformat()},
    ]


# ── Boot ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    t = threading.Thread(target=start_kafka_consumer, daemon=True)
    t.start()
    port = int(os.getenv("PORT", 5000))
    log.info("UrbanMove starting on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=os.getenv("NODE_ENV") != "production")
