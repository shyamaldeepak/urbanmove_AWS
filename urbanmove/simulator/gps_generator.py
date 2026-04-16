"""
UrbanMove GPS Simulator
=======================
Simulates a fleet of urban vehicles (buses, metros, trams, shuttles)
driving along realistic circular paths around Paris and streams GPS
events to Kafka at high throughput.

Usage:
    pip install kafka-python faker
    python3 simulator/gps_generator.py \
        --brokers localhost:9092 \
        --topic urbanmove.gps.events \
        --vehicles 100 \
        --rate 1.0
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import time
import threading
import signal
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

from faker import Faker
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
log = logging.getLogger("urbanmove.simulator")

# ── Constants ────────────────────────────────────────────────────────────────
EARTH_RADIUS_KM = 6371.0
PARIS_CENTER    = (48.8566, 2.3522)   # lat, lon

VEHICLE_TYPES = ["bus", "metro", "tram", "shuttle"]

TYPE_CONFIG = {
    "bus":     {"speed_range": (20, 55), "capacity": 80,  "passenger_factor": 0.7},
    "metro":   {"speed_range": (40, 80), "capacity": 300, "passenger_factor": 0.85},
    "tram":    {"speed_range": (25, 60), "capacity": 200, "passenger_factor": 0.75},
    "shuttle": {"speed_range": (30, 90), "capacity": 50,  "passenger_factor": 0.5},
}

STATUSES      = ["moving", "moving", "moving", "moving", "stopped", "delayed"]
MIN_VEHICLES  = 1
MAX_VEHICLES  = 500

# ── Dataclasses ──────────────────────────────────────────────────────────────
@dataclass
class GPSEvent:
    vehicle_id:  str
    type:        str
    lat:         float
    lon:         float
    speed_kmh:   float
    heading:     float
    status:      str
    route_id:    int
    passengers:  int
    capacity:    int
    timestamp:   str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")


@dataclass
class Vehicle:
    vehicle_id:   str
    vtype:        str
    route_id:     int
    # orbital path params
    orbit_lat:    float   # orbit center lat
    orbit_lon:    float   # orbit center lon
    orbit_radius: float   # km
    angle:        float   # current angle (radians)
    speed_kmh:    float
    capacity:     int
    passengers:   int
    status:       str
    heading:      float   = 0.0
    lat:          float   = 0.0
    lon:          float   = 0.0

    def __post_init__(self):
        self.lat, self.lon = _offset_latlon(
            self.orbit_lat, self.orbit_lon, self.orbit_radius, self.angle
        )

    def step(self, dt_s: float) -> GPSEvent:
        """Advance the vehicle and return a GPS event."""
        cfg = TYPE_CONFIG[self.vtype]

        # Occasionally change speed / status
        if random.random() < 0.02:
            lo, hi = cfg["speed_range"]
            self.speed_kmh = random.uniform(lo, hi)
        if random.random() < 0.005:
            self.status = random.choice(STATUSES)
        if self.status in ("stopped", "maintenance"):
            self.speed_kmh = 0.0

        # Angular velocity: arc length per second / radius
        arc_km      = (self.speed_kmh / 3600) * dt_s
        delta_angle = arc_km / self.orbit_radius if self.orbit_radius > 0 else 0
        self.angle += delta_angle
        if self.angle > 2 * math.pi:
            self.angle -= 2 * math.pi

        prev_lat, prev_lon = self.lat, self.lon
        self.lat, self.lon = _offset_latlon(
            self.orbit_lat, self.orbit_lon, self.orbit_radius, self.angle
        )
        self.heading = _bearing(prev_lat, prev_lon, self.lat, self.lon)

        # Drift passengers
        self.passengers = max(
            0,
            min(
                self.capacity,
                self.passengers + random.randint(-3, 3),
            ),
        )

        return GPSEvent(
            vehicle_id = self.vehicle_id,
            type       = self.vtype,
            lat        = round(self.lat, 6),
            lon        = round(self.lon, 6),
            speed_kmh  = round(self.speed_kmh, 1),
            heading    = round(self.heading, 1),
            status     = self.status,
            route_id   = self.route_id,
            passengers = self.passengers,
            capacity   = self.capacity,
        )


# ── Geometry helpers ─────────────────────────────────────────────────────────
def _offset_latlon(lat: float, lon: float, radius_km: float, angle: float) -> tuple[float, float]:
    """Return lat/lon displaced by radius_km in direction angle (radians)."""
    dlat = (radius_km / EARTH_RADIUS_KM) * math.cos(angle)
    dlon = (radius_km / EARTH_RADIUS_KM) * math.sin(angle) / math.cos(math.radians(lat))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing in degrees from point 1 to point 2."""
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δλ = math.radians(lon2 - lon1)
    x  = math.sin(Δλ) * math.cos(φ2)
    y  = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(Δλ)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# ── Vehicle factory ───────────────────────────────────────────────────────────
def make_fleet(n: int) -> list[Vehicle]:
    """Create n vehicles with randomised orbital paths around Paris."""
    fleet = []
    for i in range(1, n + 1):
        vtype = VEHICLE_TYPES[i % len(VEHICLE_TYPES)]
        cfg   = TYPE_CONFIG[vtype]

        # Randomise orbit center (within ~10 km of Paris center)
        base_angle  = random.uniform(0, 2 * math.pi)
        base_dist   = random.uniform(0.0, 0.08)   # degrees ≈ 8 km
        orbit_lat   = PARIS_CENTER[0] + base_dist * math.cos(base_angle)
        orbit_lon   = PARIS_CENTER[1] + base_dist * math.sin(base_angle)
        orbit_radius= random.uniform(0.5, 5.0)     # km

        lo, hi      = cfg["speed_range"]
        capacity    = cfg["capacity"]
        passengers  = int(capacity * cfg["passenger_factor"] * random.uniform(0.3, 1.0))

        fleet.append(Vehicle(
            vehicle_id    = f"VH-{i:03}",
            vtype         = vtype,
            route_id      = random.randint(1, 12),
            orbit_lat     = orbit_lat,
            orbit_lon     = orbit_lon,
            orbit_radius  = orbit_radius,
            angle         = random.uniform(0, 2 * math.pi),
            speed_kmh     = random.uniform(lo, hi),
            capacity      = capacity,
            passengers    = passengers,
            status        = random.choice(STATUSES),
        ))
    log.info("Fleet created: %d vehicles", len(fleet))
    return fleet


# ── Kafka producer ────────────────────────────────────────────────────────────
def build_producer(brokers: str, retries: int = 5) -> Optional[KafkaProducer]:
    for attempt in range(1, retries + 1):
        try:
            producer = KafkaProducer(
                bootstrap_servers = brokers.split(","),
                acks              = 1,
                compression_type  = "gzip",
                linger_ms         = 10,
                batch_size        = 32768,
                retries           = 3,
            )
            log.info("Kafka producer connected to %s", brokers)
            return producer
        except NoBrokersAvailable:
            log.warning("Kafka not reachable (attempt %d/%d). Retrying in 5s…", attempt, retries)
            time.sleep(5)
    log.error("Could not connect to Kafka. Running in DRY RUN mode (stdout only).")
    return None


# ── Worker thread ─────────────────────────────────────────────────────────────
def worker(
    fleet_slice: list[Vehicle],
    producer: Optional[KafkaProducer],
    topic: str,
    rate: float,
    stop_evt: threading.Event,
    counters: dict,
    thread_id: int,
) -> None:
    dt_s = 1.0 / rate if rate > 0 else 1.0
    lock = threading.Lock()

    while not stop_evt.is_set():
        t0 = time.monotonic()
        for vehicle in fleet_slice:
            event = vehicle.step(dt_s)
            payload = event.to_json()

            if producer:
                producer.send(topic, value=payload, key=vehicle.vehicle_id.encode())
            else:
                # Dry run – print a sample
                if random.random() < 0.05:
                    log.info("[DRY] %s", json.loads(payload))

            with lock:
                counters["sent"] += 1

        elapsed = time.monotonic() - t0
        sleep_s = max(0.0, dt_s - elapsed)
        if sleep_s:
            time.sleep(sleep_s)


# ── Stats printer ─────────────────────────────────────────────────────────────
def stats_printer(counters: dict, stop_evt: threading.Event, n_vehicles: int) -> None:
    prev = 0
    while not stop_evt.is_set():
        time.sleep(5)
        total    = counters["sent"]
        delta    = total - prev
        prev     = total
        ev_per_s = delta / 5
        log.info(
            "📊 Events sent: %d | Rate: %.1f ev/s | Vehicles: %d",
            total, ev_per_s, n_vehicles,
        )


# ── Entry point ───────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="UrbanMove GPS Vehicle Simulator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--brokers",   default="localhost:9092",        help="Kafka bootstrap servers (comma-separated)")
    p.add_argument("--topic",     default="urbanmove.gps.events",  help="Kafka topic to publish to")
    p.add_argument("--vehicles",  type=int, default=25,            help="Number of vehicles to simulate")
    p.add_argument("--rate",      type=float, default=1.0,         help="Events per second per vehicle")
    p.add_argument("--threads",   type=int, default=4,             help="Worker threads")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    n = max(MIN_VEHICLES, min(MAX_VEHICLES, args.vehicles))
    if n != args.vehicles:
        log.warning("Vehicles clamped to %d", n)

    log.info("🚀 UrbanMove GPS Simulator starting")
    log.info("   Brokers  : %s", args.brokers)
    log.info("   Topic    : %s", args.topic)
    log.info("   Vehicles : %d", n)
    log.info("   Rate     : %.1f ev/s per vehicle", args.rate)
    log.info("   Threads  : %d", args.threads)

    fleet    = make_fleet(n)
    producer = build_producer(args.brokers)

    stop_evt = threading.Event()
    counters = {"sent": 0}

    # Divide fleet across threads
    chunk = max(1, math.ceil(n / args.threads))
    workers = []
    for i in range(args.threads):
        sl  = fleet[i * chunk : (i + 1) * chunk]
        if not sl:
            break
        t = threading.Thread(
            target   = worker,
            args     = (sl, producer, args.topic, args.rate, stop_evt, counters, i),
            daemon   = True,
            name     = f"sim-worker-{i}",
        )
        t.start()
        workers.append(t)

    # Stats thread
    st = threading.Thread(
        target  = stats_printer,
        args    = (counters, stop_evt, n),
        daemon  = True,
        name    = "stats-printer",
    )
    st.start()

    # Graceful shutdown on SIGINT / SIGTERM
    def _shutdown(sig, frame):
        log.info("Shutting down… (Ctrl+C again to force)")
        stop_evt.set()
        if producer:
            producer.flush(timeout=5)
            producer.close()
        log.info("✅ Simulator stopped. Total events sent: %d", counters["sent"])
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("✅ Simulator running. Press Ctrl+C to stop.")
    for t in workers:
        t.join()


if __name__ == "__main__":
    main()
