-- Latest sensor health per gateway, component and kind (from traffic.health.v2).
CREATE TABLE gateway_health (
    gateway_id TEXT NOT NULL,
    component TEXT NOT NULL,
    kind TEXT NOT NULL,
    count BIGINT NOT NULL,
    first_sequence BIGINT NOT NULL,
    last_sequence BIGINT NOT NULL,
    first_event_time TIMESTAMPTZ,
    last_event_time TIMESTAMPTZ,
    detail TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gateway_id, component, kind)
);
