-- Per-minute history of each device's 60 s window (from traffic.features.v2)
-- for the device traffic chart. The latest window within a minute wins;
-- the projector prunes rows older than 24 h.
CREATE TABLE device_feature_history (
    device_key TEXT NOT NULL,
    minute TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    features JSONB NOT NULL,
    PRIMARY KEY (device_key, minute)
);
CREATE INDEX device_feature_history_minute_idx ON device_feature_history (minute);
