# Incident API

FastAPI service over the incident projector's tables
(`images/incident-projector`). Serves the live page in `app/static` at `/live`.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v2/incidents?active=true` | Snapshot of incidents (active only by default) plus the feed `cursor` it corresponds to (one REPEATABLE READ transaction). |
| `GET /api/v2/incidents/changes?after=<cursor>&limit=` | Feed rows with `cursor > after`, in order. `410` when `after` is older than the retained feed (7 days): reload the snapshot. |
| `GET /api/v2/health` | Latest sensor health per gateway, component and kind. |
| `WS /ws/v2/incidents?after=<cursor>` | `health`, then catch-up (`change` messages, or `reset` + `snapshot` for an expired cursor; `snapshot` when `after` is omitted), then `live`, then `change` / `health` as they happen and `ping` every 20 s. |
| `GET /healthz` | Database reachable. |

PostgreSQL `NOTIFY` is only a wake-up: after every notification the API reads
`cursor > last sent`, and it `LISTEN`s before catching up, so a client gets every
change exactly once and in order across catch-up and live. This relies on a
single projector writing the feed.

Each change carries the transition JSON (`status`, `revision`, horizons,
`evidence_event_time_ms`, `evidence_ingest_time_ms`, `detected_at_ms`) and
`committed_at_ms`, so a client can compute the evidence-to-browser delay.

## Configuration

`DATABASE_URL` (required), `PORT` (default 8080), `STATIC_DIR`.

## Development

```sh
pip install -r images/incident-api/requirements.txt -r images/incident-api/requirements-dev.txt \
  -r images/incident-projector/requirements.txt -r images/incident-projector/requirements-build.txt
PROJECTOR_TEST_DSN=postgresql://postgres:test@localhost:55432/postgres \
  python -m unittest tests.test_incident_projector tests.test_incident_api -v
docker build images/incident-api
```
