# pi-agents

Agent-based IoT threat detection system. Gateway RPi acts as WiFi AP + traffic collector + ML anomaly detector running on K3s cluster.

---

## Development & Testing

### Local run (no hardware)
```bash
# Uses podman-compose (not docker — only podman is installed)
podman-compose up --build
# API:       http://localhost:8080  (docs: /docs)
# Dashboard: http://localhost:3000
```

**Podman machine clock drift**: The podman machine VM clock can lag behind the host (seen as 2h drift). This causes `apt-get update` to fail with "Release file is not valid yet". Fix:
```bash
CORRECT_TIME=$(date -u +"%Y-%m-%d %H:%M:%S") && podman machine ssh "sudo date -s '$CORRECT_TIME UTC'"
```

### With gateway hardware (Linux only — uses hostNetwork + privileged)
```bash
docker-compose --profile gateway up --build
```

### Running tests
```bash
# All tests (same as CI) — run from repo root
python -m unittest discover -v

# Single file
python -m unittest tests.test_ml_logic -v

# Single class or method
python -m unittest tests.test_ml_logic.TestFeatureExtractor.test_single_flow_produces_one_bucket -v
```

**No pytest, no conftest.py, no Makefile.** Tests live in `tests/` at the repo root and are discovered via `python -m unittest discover`.

### Installing test dependencies
There is **no top-level `requirements.txt` or `pyproject.toml`**. Each image has its own `requirements.txt`. CI installs only these two before running tests:
```bash
pip install -r images/gateway-agent/requirements.txt
pip install -r images/ml-pipeline/requirements.txt
```
If a test imports from another image, install that image's requirements manually.

### CI pipeline (`validate.yml`)
1. `python -m compileall -q images` — syntax-checks all Python under `images/`
2. Install gateway-agent + ml-pipeline requirements
3. `python -m unittest discover -v`
4. (parallel) `kubectl kustomize k8s/gateway | kubectl apply --dry-run=client --validate=false`
5. `e2e` job builds cached Docker images with Buildx + GHA cache, starts the local stack with `docker compose`, then runs Playwright

### Playwright in CI
- `validate.yml` has an `e2e` job for end-to-end coverage
- In CI, use `docker compose` (GitHub runners have Docker, not Podman)
- The job runs two Playwright suites:
  - Mocked UI tests: `npx playwright test --project=chromium`
  - Live integration tests: `npx playwright test --project=integration --workers=1`
- Docker image layers are cached with `docker/build-push-action` + `type=gha`
- The job is informational (`continue-on-error: true`) and uploads `playwright-report/` + `test-results/` on failure

`docker-build.yml` triggers only on push to `main` or PRs touching `images/**`. Builds `linux/amd64,linux/arm64` on main push; `linux/amd64` only on PRs (no push). Image tags: `latest` + `sha-{first 8 chars of SHA}`.

### UI tests with Playwright
The `playwright-cli` skill is available. Use it to generate and run browser tests against the dashboard (`http://localhost:3000`).

Playwright tests live in `tests/ui/`. Run them with:

```bash
# Stack must be running first (or set reuseExistingServer: false in playwright.config.ts)
cd tests/ui
npx playwright test          # all Playwright projects
npx playwright test --project=chromium
npx playwright test --project=integration --workers=1
npm run test:mocked
npm run test:integration
npx playwright test --headed # headed
npx playwright test --debug  # debug mode
```

Test files:
- `index.spec.ts` — index page (`/`): nav, metric cards, tabs, alert feed
- `device.spec.ts` — device detail page (`/devices/{id}`): identity, risk, model table, block, training config
- `gateway.spec.ts` — WiFi AP page (`/gateway`): form fields, four action buttons, POST flows
- `integration/api.spec.ts` — live gateway-api + dashboard proxy checks with no route mocks
- `integration/pages.spec.ts` — live dashboard page checks with the real local stack

Mocks: `fixtures/api-mock.ts` stubs all dashboard proxy routes (`/api/*`) so mocked tests work without real IoT hardware. Routes are mocked at `localhost:3000/api/*` (the dashboard's own proxy), not at gateway-api directly.

Mocked tests that assert content loaded through HTMX partials must also stub the browser-visible `/partial/*` routes. Those fragments are rendered server-side by the dashboard, so mocking only `page.route('**/api/*')` does not change HTML returned by `/partial/devices`, `/partial/alerts`, `/partial/timeline`, or `/partial/top-talkers`.

Integration tests: `tests/ui/integration/` does not mock `/api/*` and instead exercises the real stack on `localhost:3000` and `localhost:8080`.

**Quirks learned from live inspection:**
- Alpine.js tabs use class `tab-active`, not `aria-selected` — check `toHaveClass(/tab-active/)`
- `"ML Model Health"` and `"Behavior Alerts"` are `<div class="text-xs">`, not heading elements — use `getByText()`
- Gateway form `<label>` and `<input>` are not linked via `for`/`id` — target by `locator('input[name="ssid"]')` etc.
- Device page has 4 comboboxes total: model-select, training-data timerange, ML health model, alert source filter
- Device `protocol-signals` UI renders `signals[]` rows with `{ label, value, note }`; a flat `{ dns_failures_24h, icmp_echo_requests_24h }` mock will not populate the panel
- `metrics/ml-status` device rows expose `training_metrics[]` for the ML health table; old flat metric fields are not enough for realistic UI mocks

---

## Critical Gotchas

### `app` package name collision
All five images use `app` as their Python package name. Tests that import from multiple images **must** clear `sys.modules` between imports:
```python
import sys
sys.path.insert(0, "images/ml-pipeline")
import app.train
del sys.modules["app"]  # and all app.* submodules
sys.path.insert(0, "images/gateway-api")
import app.main
```
See `tests/test_ml_logic.py` `_setup_ml_path()` for the pattern.

### Minimum training samples = 30 (not 20)
Code default in `train.py` is `MIN_TRAINING_SAMPLES=30`. The "20" in some docs is stale. Falls back to global model if per-device count is below threshold.

### `bytes_received` is always 0
Collector only writes `bytes_sent` (= `frame.len` from tshark). `bytes_received` is inserted as 0. Feature extraction computes `total_bytes = bytes_sent + bytes_received` — do not assume symmetrical counts.

### `collector` metrics endpoint intentionally absent
Removed for MVP to avoid conflicts with `node_exporter`. **Do not re-add** a `/metrics` endpoint to collector.

### `ENABLE_APPLY=false` by default in gateway-agent
The WiFi AP will not start unless `ENABLE_APPLY=true`. The prod overlay (`k8s/overlays/gateway-prod`) sets this. Local docker-compose uses the default (safe).

### Legacy `model_metadata.version` column
Older production DBs have `version TEXT NOT NULL` not in newer CREATE TABLE statements. Always insert `version="1.0"` when writing to `model_metadata`.

### Dashboard TailwindCSS
`dist.css` is pre-built into the Docker image. For local development outside Docker, rebuild styles:
```bash
cd images/dashboard && npm install && npm run build:css
```

### All K8s pods require resource limits
Cluster policy enforces limits. See README for exact values per workload. Do not create or update manifests without `resources.limits`.

### Gateway node label required
```bash
kubectl label node <worker-1-name> node-role.kubernetes.io/gateway=true
```

---

## Architecture

### Package layout
```
images/
  gateway-api/     # FastAPI REST + WebSocket; entrypoint: app.main:app (uvicorn)
  collector/       # tcpdump/tshark capture; entrypoint: python -m app
  ml-pipeline/     # Training (app.train) + inference (app.inference) — same image
  gateway-agent/   # hostapd/dnsmasq WiFi AP controller; FastAPI on port 7000
  dashboard/       # FastAPI + HTMX + TailwindCSS; port 3000 external → 8080 internal
k8s/
  base/            # Namespace + PVC
  gateway/         # All workload Deployments / CronJobs / Ingress
  overlays/gateway-prod/  # Sets ENABLE_APPLY=true (activates WiFi AP)
tests/             # Flat unittest directory at repo root (not inside any image)
```

### K8s deploy order
```bash
kubectl apply -k k8s/base
kubectl apply -k k8s/gateway
# For WiFi AP only:
kubectl apply -k k8s/overlays/gateway-prod
```

### ML ensemble
4 models per device: IF (40%), LOF (30%), OCSVM (20%), Autoencoder (10%). Anomaly triggered by majority vote (≥2/4). Old 8-feature models are backward-compatible (`n_features_in_` used to infer feature count). Current feature set has **12 features** (added `protocol_entropy`, `dst_ip_entropy`, `dns_to_total_ratio`, `iat_std` on top of the original 8).

### Risk score composition (0–100)
```
ml_risk          (0–35)   weighted-avg decision score across 4 models
behavior_risk    (0–35)   9 heuristic alert types, capped per type
protocol_risk    (0–20)   DNS/ICMP protocol-level signals
correlation_bonus (0–15)  ML + heuristics firing together
= final_risk     (0–100)  stored in devices.risk_score
```

### Routing quirk
`POST /api/v1/alerts/broadcast` is mounted directly on `app` in `main.py` (not via a router file), but still lives under `settings.api_prefix`.

### WebSocket
`WS /ws/alerts` is echo-only. The dashboard gets real alert data by polling `GET /api/v1/alerts` (unified feed: anomalies + behavior_alerts).

### SQLite
Single file at `DATABASE_PATH` (`/data/iot-security.db`) shared by collector, ml-inference, and gateway-api. WAL mode + `busy_timeout=5000ms` must be set on every connection.

### collector constraints
Requires `CAP_NET_ADMIN` + `CAP_NET_RAW`, `hostNetwork: true`, `INTERFACE=wlan0`. Will not function without root/privileged on Linux with a real NIC.

### ml-trainer vs ml-inference
- **ml-trainer**: K8s CronJob (every 30 min); docker-compose `restart: on-failure` (runs once and exits)
- **ml-inference**: K8s Deployment + docker-compose always-on loop; runs every `INFERENCE_INTERVAL` seconds (default 300)

---

## Ingress

| Service | Host | TLS Issuer |
|---------|------|------------|
| gateway-api | `iot-api.homelab.kacperjarocki.dev` | `letsencrypt-http-prod` (Cloudflare DNS-01) |
| dashboard | `iot-dashboard.homelab.kacperjarocki.dev` | same |

`ingressClassName: traefik` — standard Kubernetes `Ingress` (not IngressRoute).

---

## Key Environment Variables

| Variable | Default | Service |
|----------|---------|---------|
| `DATABASE_PATH` | `/data/iot-security.db` | all |
| `MODEL_PATH` | `/data/models` | ml-pipeline |
| `GATEWAY_AGENT_URL` | `http://gateway-agent.iot-security:7000` | gateway-api |
| `ENABLE_APPLY` | `false` | gateway-agent |
| `INFERENCE_INTERVAL` | `300` | ml-inference |
| `MIN_TRAINING_SAMPLES` | `30` | ml-trainer |
| `FEATURE_BUCKET_MINUTES` | `5` | ml-pipeline |
| `INTERFACE` | `wlan0` | collector |

Config loaded via `pydantic_settings.BaseSettings`; supports `.env` in the working directory.

---

## Git Workflow

**Always commit and push after completing a task.** Never leave changes uncommitted.

Work on a dedicated branch for each task. Do not push straight to `main`.

```bash
git checkout -b <type>/<short-description>
git add <files>
git commit -m "<type>: <short description>"
git push
```

- Branch naming: use `<type>/<short-description>` such as `fix/playwright-e2e-stability` or `docs/update-dashboard-notes`
- Commit message types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`
- Prefer focused commits with clear intent; avoid mixing unrelated changes in one commit
- Push every session — don't accumulate local-only commits
- Prefer opening a PR with a concise title and summary of user-visible or developer-impacting changes
- PR descriptions should call out test coverage and any documentation or workflow updates
