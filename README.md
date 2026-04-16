# UrbanMove

<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/65a9d563-61a7-4ec5-990d-50c589601610" />

UrbanMove is a cloud-native smart mobility demo platform that tracks public transit vehicles in near real time.

It includes:
- A Flask API and dashboard UI
- Kafka-based event ingestion
- A stream processor writing to PostgreSQL and Redis, and forwarding critical alerts to SNS
- A GPS simulator to generate fleet telemetry

## Project Explanation

UrbanMove simulates and monitors a city transport network.

How data moves through the system:
1. The simulator publishes GPS events to Kafka.
2. The stream processor consumes Kafka topics and:
	- Stores GPS data in PostgreSQL
	- Caches congestion in Redis
	- Sends critical alert notifications to SNS
3. The Flask app serves APIs and the dashboard UI.
4. The dashboard polls APIs every few seconds and visualizes vehicles, alerts, and congestion.

This gives you a full event-driven pipeline for a smart mobility use case.

## Project Structure

- urbanmove/app.py: Flask app and REST API (/api/vehicles, /api/stats, /api/alerts, etc.)
- urbanmove/consumer.py: Kafka stream processor (GPS persistence, congestion cache, SNS alerts)
- urbanmove/templates/index.html: Real-time dashboard with Leaflet map
- urbanmove/simulator/gps_generator.py: GPS event simulator for vehicle telemetry
- urbanmove/schema.sql: PostgreSQL schema and seed data
- docker-compose.yml: Local development stack (Postgres, Redis, Kafka, app, processor, simulator)
- UrbanMove_AWS_Setup_Guide.md: Step-by-step AWS deployment guide

## Setup

Choose one setup path:
- Path A: Docker (recommended, fastest)
- Path B: Manual Python run

## Setup Path A: Docker (Recommended)

Prerequisites:
- Docker Desktop installed and running
- Docker Compose available

From the urban folder, run:

```bash
docker compose up --build
```

Services started:
- Postgres: localhost:5433
- Redis: localhost:6379
- Kafka: localhost:9092
- App: http://localhost:5000

Open the dashboard:
- http://localhost:5000

Stop and remove containers with volumes:

```bash
docker compose down -v
```

## Setup Path B: Manual Python Run

Prerequisites:
- Python 3.11 or newer
- PostgreSQL, Redis, and Kafka running and reachable

```bash
cd urban/urbanmove
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Set required environment variables in .env (see urbanmove/.env template).

## Environment Variables

Main variables used by the app and processor:
- DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
- REDIS_HOST, REDIS_PORT
- KAFKA_BROKERS
- AWS_REGION, SNS_TOPIC_ARN
- PORT, NODE_ENV, JWT_SECRET

Use the template file at urbanmove/.env and replace all placeholder values.

## Verify Setup

After startup, verify:
- Health endpoint: GET /health
- Vehicles endpoint: GET /api/vehicles
- Dashboard page loads in browser at http://localhost:5000

If Kafka is unavailable, the app still returns mock vehicles so the UI remains usable.

## API Endpoints

- GET /health
- GET /api/vehicles
- GET /api/vehicles/<vehicle_id>
- GET /api/routes
- GET /api/congestion
- GET /api/alerts
- GET /api/stats
- POST /api/publish (publish test GPS event to Kafka)

## Stream Processor Behavior

consumer.py consumes:
- urbanmove.gps.events -> batch insert into PostgreSQL gps_events
- urbanmove.congestion -> Redis cache keys urbanmove:congestion:*
- urbanmove.alerts -> publishes critical alerts to SNS (if SNS_TOPIC_ARN is configured)

## AWS Deployment

For full cloud deployment, use:
- UrbanMove_AWS_Setup_Guide.md

It covers VPC, RDS, ElastiCache, MSK, S3, Cognito, EC2, ALB, CloudWatch, Route 53, and DR region setup.

## Security Notes

- Replace demo credentials and secrets before production use.
- Do not commit .env files with real credentials.
- Rotate JWT_SECRET and cloud credentials regularly.

## Troubleshooting

- App not starting: check DB/Redis/Kafka host and ports in .env.
- No live events: check Kafka broker reachability and topic creation.
- Empty routes or congestion: apply schema.sql to PostgreSQL.
- Alerts not sent: verify SNS_TOPIC_ARN and IAM permissions.

## Useful Commands

From urban root:

```bash
docker compose up --build
docker compose logs -f app
docker compose logs -f processor
docker compose down -v
```
