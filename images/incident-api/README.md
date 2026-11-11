# Incident API

FastAPI service over the incident projector's tables
(`images/incident-projector`). It also serves the web UI built from `ui/`
(Svelte 5 + TypeScript + Vite; the Dockerfile builds it into `app/static`):

- `/`: device overview. Summary tiles, search and state filters, one row per
  device with hostname, IP, state (alert, clean, idle, no traffic), the latest
  60 s and 15 min window stats, and incident counts. Clicking a device opens a
  side panel (`/?device=<key>`) with its details, a traffic chart (failed targets
  against the rule threshold and flows, last 1/6/24 h, incident episodes shaded),
  its incident history and, when enabled, a block/unblock control. Each incident
  is named and explained in plain words (e.g. "Port scan: 241 refused or
  unanswered targets in 60 s (opens at 20)"); expanding one shows how the rule
  works and a timeline of its transitions.
- `/live/`: live incidents over the WebSocket, with severity filters, a
  resolved toggle, threshold meters and highlighted changes. Devices are shown
  by hostname and open their history in the app.

Both pages share one live connection: the header shows its state and the
number of open incidents, and the device list refreshes when an incident
changes. The theme follows the OS or a manual light/dark choice.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v2/incidents?active=true&device=<key>` | Snapshot of incidents (active only by default, optionally for one device key) plus the feed `cursor` it corresponds to (one REPEATABLE READ transaction). |
| `GET /api/v2/incidents/<id>/transitions` | Every transition of one incident, in revision order. |
| `GET /api/v2/devices` | Devices joined by MAC from `device_features` (latest window per horizon), incident counts, and the gateway-agent's DHCP leases (hostname, IP; cached 10 s). `leases.available` is false when the agent is not configured or unreachable. |
| `GET /api/v2/devices/<device_key>/history?minutes=60` | One point per minute of the device's 60 s window (flows, targets, failed targets, bytes), oldest first; 5–1440 minutes, kept 24 h by the projector. |
| `POST` / `DELETE /api/v2/devices/<mac>/block` | Blocks / unblocks a device through the gateway-agent (`/block`). `403` unless `DEVICE_BLOCKING=true`, `503` when the agent is unreachable. `GET /api/v2/devices` reports `actions.block` and a `blocked` flag per device. |
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

`DATABASE_URL` (required), `PORT` (default 8080), `STATIC_DIR`,
`GATEWAY_AGENT_URL` (optional, e.g. `http://gateway-agent:7000`; chart value
`incidents.api.gatewayAgentUrl`, set in prod only), `DEVICE_BLOCKING` (chart
value `incidents.api.deviceBlocking`, default `false`, on in prod). The API has no
authentication, so enable blocking only when the dashboard is reachable by
trusted users alone. A block is an iptables rule on the gateway and lasts until
the gateway's firewall is reset.

## Development

```sh
pip install -r images/incident-api/requirements.txt -r images/incident-api/requirements-dev.txt \
  -r images/incident-projector/requirements.txt -r images/incident-projector/requirements-build.txt
PROJECTOR_TEST_DSN=postgresql://postgres:test@localhost:55432/postgres \
  python -m unittest tests.test_incident_projector tests.test_incident_api -v
docker build images/incident-api

# UI (proxies /api and /ws to an incident-api on :8080)
cd images/incident-api/ui && npm ci && npm run dev
npm run format:check && npm run lint && npm run check && npm test
```
