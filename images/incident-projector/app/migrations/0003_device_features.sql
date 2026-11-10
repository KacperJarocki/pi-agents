-- Latest rolling-window view per device and horizon (from traffic.features.v2).
-- Feeds the device overview; one row per (device_key, horizon_seconds).
CREATE TABLE device_features (
    device_key TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    feature_version TEXT NOT NULL,
    features JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_key, horizon_seconds)
);
