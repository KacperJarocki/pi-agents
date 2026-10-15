# Incident projector

Consumes `traffic.detections.v2` (`DetectionTransition`) and maintains incident
state in PostgreSQL for the incident API and the live page.

Per transition, one transaction:

1. `INSERT INTO incident_transitions … ON CONFLICT DO NOTHING` — a redelivered
   or replayed transition stops here;
2. upsert `incidents`, only when the incoming revision is newer than the stored
   one (`active` is false after `RESOLVE`);
3. append to `incident_feed` (`cursor` = position) with the transition as JSON;
4. `NOTIFY incident_feed, '<cursor>'`.

The Kafka offset is committed only after the database commit, so a crash in
between replays the message as a no-op. It reads `read_uncommitted` (the Flink
sink is transactional) and relies on the transition primary key instead of
waiting for Flink checkpoints. Database errors (e.g. a CNPG failover) are
retried on a new connection without committing the offset. Undecodable messages
are logged, counted and skipped. Feed rows older than 7 days are pruned hourly;
older cursors fall back to a snapshot in the incident API.

Migrations live in `app/migrations` and run at start (advisory-locked).

## Configuration

`DATABASE_URL` (required; the CNPG `pi-agents-pg-app` Secret's `uri`),
`KAFKA_BOOTSTRAP`, `DETECTIONS_TOPIC`, `GROUP_ID`, `HEARTBEAT_FILE`.

## Development

```sh
pip install -r images/incident-projector/requirements.txt -r images/incident-projector/requirements-build.txt
python images/incident-projector/generate_proto.py      # app/events_pb2.py from schemas/events/v2
podman run -d --name pg -e POSTGRES_PASSWORD=test -p 55432:5432 docker.io/library/postgres:17.6
PROJECTOR_TEST_DSN=postgresql://postgres:test@localhost:55432/postgres \
  python -m unittest tests.test_incident_projector -v
docker build -f images/incident-projector/Dockerfile .  # from the repository root
```
