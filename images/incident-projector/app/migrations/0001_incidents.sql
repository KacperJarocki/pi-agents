-- Incident state projected from traffic.detections.v2.

-- Every transition ever applied; the primary key makes redelivery a no-op.
CREATE TABLE incident_transitions (
    transition_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    revision BIGINT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX incident_transitions_incident_idx ON incident_transitions (incident_id, revision);

-- Current state per incident: only a newer revision may replace a row.
CREATE TABLE incidents (
    incident_id TEXT PRIMARY KEY,
    device_key TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    threat_family TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    status TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    severity TEXT NOT NULL,
    revision BIGINT NOT NULL,
    episode_start TIMESTAMPTZ NOT NULL,
    evidence_event_time TIMESTAMPTZ NOT NULL,
    evidence_ingest_time TIMESTAMPTZ,
    detected_at TIMESTAMPTZ NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_transition JSONB NOT NULL
);
CREATE INDEX incidents_active_idx ON incidents (active, detected_at DESC);

-- Ordered change feed for browsers: cursor = position, NOTIFY carries it.
CREATE TABLE incident_feed (
    cursor BIGSERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL,
    revision BIGINT NOT NULL,
    payload JSONB NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX incident_feed_committed_idx ON incident_feed (committed_at);
