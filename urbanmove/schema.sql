-- UrbanMove PostgreSQL Schema
-- Run: psql -h $DB_HOST -U admin -d urbanmove < schema.sql

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Users ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email       TEXT UNIQUE NOT NULL,
    cognito_sub TEXT UNIQUE,
    role        TEXT NOT NULL DEFAULT 'user'  CHECK (role IN ('admin','operator','user')),
    city        TEXT NOT NULL DEFAULT 'Paris',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Routes ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS routes (
    id            SERIAL PRIMARY KEY,
    route_name    TEXT NOT NULL,
    route_type    TEXT NOT NULL DEFAULT 'bus' CHECK (route_type IN ('bus','tram','metro','shuttle')),
    origin        TEXT NOT NULL,
    destination   TEXT NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    vehicle_count INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Vehicles ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id  TEXT PRIMARY KEY,
    type        TEXT NOT NULL DEFAULT 'bus',
    route_id    INT REFERENCES routes(id),
    status      TEXT NOT NULL DEFAULT 'moving' CHECK (status IN ('moving','stopped','delayed','maintenance')),
    capacity    INT NOT NULL DEFAULT 80,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── GPS Events (time-series log) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gps_events (
    id          BIGSERIAL PRIMARY KEY,
    vehicle_id  TEXT NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    speed_kmh   NUMERIC(5,1) NOT NULL DEFAULT 0,
    heading     NUMERIC(5,1),
    passengers  INT DEFAULT 0,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gps_vehicle_time ON gps_events(vehicle_id, recorded_at DESC);

-- ── Congestion Zones ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS congestion_zones (
    id          SERIAL PRIMARY KEY,
    zone_name   TEXT NOT NULL,
    level       TEXT NOT NULL DEFAULT 'low' CHECK (level IN ('low','medium','high','critical')),
    lat_center  DOUBLE PRECISION,
    lon_center  DOUBLE PRECISION,
    radius_m    INT DEFAULT 500,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Alerts ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id          BIGSERIAL PRIMARY KEY,
    type        TEXT NOT NULL,
    message     TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warning','critical')),
    vehicle_id  TEXT,
    zone_name   TEXT,
    resolved    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────────────────────
-- DUMMY DATA
-- ────────────────────────────────────────────────────────────────────────────

-- Routes
INSERT INTO routes (route_name, route_type, origin, destination, active, vehicle_count) VALUES
    ('Line A',      'metro',   'La Défense',     'Château de Vincennes', TRUE,  8),
    ('Line B',      'metro',   'Robinson',       'Roissy–CDG',           TRUE,  6),
    ('Line C',      'metro',   'Versailles',     'Massy-Palaiseau',      TRUE,  7),
    ('Tram 3A',     'tram',    'Pont du Garigliano', 'Porte de Vincennes', TRUE, 5),
    ('Tram 3B',     'tram',    'Porte de la Chapelle', 'Porte de Vincennes', TRUE, 4),
    ('Bus 21',      'bus',     'Gare du Nord',   'Opéra',                TRUE,  3),
    ('Bus 38',      'bus',     'Gare du Nord',   'Porte d''Orléans',     TRUE,  4),
    ('Bus 73',      'bus',     'Opéra',          'Musée d''Orsay',       TRUE,  3),
    ('Shuttle S1',  'shuttle', 'CDG Airport',    'Paris Centre',         TRUE,  2),
    ('Shuttle S2',  'shuttle', 'Orly Airport',   'Paris Centre',         TRUE,  2),
    ('Metro 1',     'metro',   'La Défense',     'Château de Vincennes', TRUE,  10),
    ('Metro 14',    'metro',   'Olympiades',     'Saint-Lazare',         TRUE,  9)
ON CONFLICT DO NOTHING;

-- Vehicles
INSERT INTO vehicles (vehicle_id, type, route_id, status, capacity) VALUES
    ('VH-001', 'metro',   1, 'moving',  300),
    ('VH-002', 'metro',   1, 'moving',  300),
    ('VH-003', 'tram',    4, 'moving',  200),
    ('VH-004', 'bus',     6, 'moving',   80),
    ('VH-005', 'bus',     6, 'stopped',  80),
    ('VH-006', 'shuttle', 9, 'moving',   50),
    ('VH-007', 'metro',   2, 'delayed', 300),
    ('VH-008', 'bus',     7, 'moving',   80),
    ('VH-009', 'tram',    5, 'moving',  200),
    ('VH-010', 'metro',  11, 'moving',  300)
ON CONFLICT DO NOTHING;

-- Congestion zones
INSERT INTO congestion_zones (zone_name, level, lat_center, lon_center, radius_m) VALUES
    ('Châtelet–Les Halles',  'high',     48.8604,  2.3467,  600),
    ('La Défense',           'medium',   48.8918,  2.2432,  800),
    ('Gare du Nord',         'medium',   48.8809,  2.3553,  400),
    ('Étoile / Arc',         'low',      48.8738,  2.2950,  500),
    ('Opéra',                'high',     48.8718,  2.3316,  350),
    ('Nation',               'low',      48.8485,  2.3967,  400)
ON CONFLICT DO NOTHING;

-- Alerts
INSERT INTO alerts (type, message, severity, vehicle_id, zone_name) VALUES
    ('delay',       'VH-007 running 8 min late on Line B',             'warning',  'VH-007', NULL),
    ('congestion',  'Heavy congestion at Châtelet–Les Halles',          'critical', NULL,     'Châtelet–Les Halles'),
    ('breakdown',   'VH-005 stopped at Gare du Nord – awaiting crew',  'warning',  'VH-005', 'Gare du Nord'),
    ('info',        'Tram 3A on time across all stops',                 'info',     NULL,     NULL),
    ('congestion',  'Moderate congestion at Opéra – suggest reroute',  'warning',  NULL,     'Opéra')
ON CONFLICT DO NOTHING;
