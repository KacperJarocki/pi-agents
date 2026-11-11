# pi-agents

Real-time IoT threat detection on a K3s cluster of 5× Raspberry Pi 5 (8 GB, NVMe). The gateway Pi is the Wi-Fi AP and capture point; a Rust sensor publishes traffic events to Kafka, Flink evaluates per-device sliding windows, and incidents are pushed to the browser.

The thesis-era batch pipeline (collector → SQLite → ml-trainer/ml-inference → gateway-api → dashboard) has been removed. Its last state is the git tag `thesis-final`; check it out for anything thesis-related.

---

## Where things are

```
images/gateway-agent/   # Wi-Fi AP controller (hostapd/dnsmasq), FastAPI on port 7000
images/incident-api/    # Incident API (FastAPI) + ui/ (Svelte 5 web UI)
sensors/rust-sensor/    # Rust capture sensor (Cargo crate)
schemas/events/         # Protobuf event contracts; v2 is current, v1 frozen
charts/pi-agents/       # Helm chart: streaming config, Strimzi Kafka, Flink
k8s/base/               # Namespace + streaming ConfigMap
k8s/gateway/            # gateway-agent Deployment/Service
k8s/overlays/gateway-prod/  # Sets ENABLE_APPLY=true (activates the AP)
k8s/argocd/             # Argo CD: operator Applications, gateway-agent, pi-agents ApplicationSet (dev+prod)
k8s/envs/               # base.yaml + <env>/values.yaml (sizing) + <env>/images.yaml (promotable tags)
k8s/values/             # Helm values of the operator Applications
scripts/                # Contract validator, capture benchmark, traffic generators
openspec/               # Spec-driven changes; the active plan lives here
tests/                  # Flat unittest directory at repo root
```

The work plan is the OpenSpec change `openspec/changes/realtime-sliding-threat-detection/` (proposal, design, tasks, specs). `docs/STREAMING-ROADMAP.md` is the wider backlog.

---

## Development & Testing

### Running tests
```bash
# All Python tests (same as CI) — run from repo root
python -m unittest discover -v

# Single file / method
python -m unittest tests.test_event_contract_validator -v
python -m unittest tests.test_gateway_agent_render.TestGatewayAgentRender.test_render_contains_expected_fields -v
```

**No pytest, no conftest.py, no Makefile.** Tests are discovered via `python -m unittest discover`. There is no top-level `requirements.txt`; CI installs `images/gateway-agent/requirements.txt` only.

### Other checks
```bash
python scripts/validate_event_contract.py          # validates v1 and v2 without protoc
cargo fmt --manifest-path sensors/rust-sensor/Cargo.toml -- --check
cargo test --manifest-path sensors/rust-sensor/Cargo.toml
cargo check --manifest-path sensors/rust-sensor/Cargo.toml --target aarch64-unknown-linux-gnu
helm lint charts/pi-agents -f charts/pi-agents/values-flink-longhorn.yaml
helm lint charts/pi-agents -f charts/pi-agents/values-flink-s3.yaml
kubectl kustomize k8s/gateway
for env in dev prod; do helm template pi-agents charts/pi-agents -n iot-security-$env -f k8s/envs/base.yaml -f k8s/envs/$env/values.yaml -f k8s/envs/$env/images.yaml > /dev/null; done
helm dependency build charts/strimzi-operator && helm template strimzi-operator charts/strimzi-operator -n strimzi-system -f k8s/values/strimzi-operator.yaml
```

### Pre-commit
Hooks live in `.pre-commit-config.yaml` (ruff lint + format with `ruff.toml`, shellcheck, actionlint for workflows, whitespace/EOF/line endings, YAML/JSON/TOML syntax, private keys, executable bits, the event contract validator, `cargo fmt`, and Prettier/ESLint for the UI). Install once per clone; they then fix or block staged files on every commit:
```bash
pre-commit install                 # brew install pre-commit / pip install pre-commit
pre-commit run --all-files         # whole repo, same as the CI job `pre-commit`
```
The UI hooks need `npm ci` in `images/incident-api/ui`. Keep formatter-sensitive literals that tests assert on inside `# fmt: off` / `# fmt: on`.

### Local run
Only `gateway-agent` is in `docker-compose.yml`, behind the `gateway` profile (Linux only: host network + privileged). Locally only `podman` is installed, not docker.

**Podman machine clock drift**: the VM clock can lag the host, which breaks `apt-get update` with "Release file is not valid yet". Fix:
```bash
CORRECT_TIME=$(date -u +"%Y-%m-%d %H:%M:%S") && podman machine ssh "sudo date -s '$CORRECT_TIME UTC'"
```

### CI
- `validate.yml`: `compileall images`, unit tests, Helm lint/template of every values profile, `kubectl kustomize k8s/gateway` + client dry run; `projector` job runs the incident projector and API tests against a `postgres:17.6` service (`PROJECTOR_TEST_DSN`); `ui` job runs `npm audit`, Prettier, ESLint, `svelte-check` and Vitest for `images/incident-api/ui`, then the Playwright tests.
- `flink-job.yml`: `./mvnw verify` for `flink/detection-job` (MiniCluster and operator-harness tests).
- `rust-sensor.yml`: fmt, test and an aarch64 check for the sensor.
- `streaming-contracts.yml`: event contract validator and its tests.
- `docker-build.yml`: builds `images/*` on PRs (amd64, no push) and on `main` (amd64 + arm64, push to `ghcr.io`, tags `latest` and `sha-<8 chars>`). Every build is scanned with Trivy (`scripts/trivy-scan.sh`, fixable MEDIUM+ vulnerabilities): the job summary lists findings, and on `main` the arm64 image's SARIF goes to code scanning (Security tab). A fixable CRITICAL fails the build (and the dev deploy); fix it by bumping the base image (Dependabot proposes weekly bumps) or accept it in `.trivyignore` with a reason.
- `image-scan.yml`: weekly Trivy scan of the images prod runs (`k8s/envs/prod/images.yaml`, `gateway-agent:latest`) into code scanning; fails (and notifies) on a fixable CRITICAL.
- `validate.yml` job `pre-commit`: runs the pre-commit hooks on all files (minus cargo fmt and the UI hooks, which run in their own jobs).

### Web UI
`images/incident-api/ui/` is the browser UI: Svelte 5 + TypeScript built by Vite 8 into static files (`/` device overview, `/live/` live incidents, one shared bundle). The incident-api Dockerfile builds it in a Node stage and copies `dist/` into `app/static`, which is not in git. Tooling: ESLint 10 (flat config, typescript-eslint, eslint-plugin-svelte), Prettier 3 with the Svelte plugin, `svelte-check`, Vitest. CI fails on `npm audit` findings of moderate or higher.
```bash
cd images/incident-api/ui && npm ci
npm run dev           # proxies /api and /ws to an incident-api on :8080 (API_URL to override)
npm run format:check && npm run lint && npm run check && npm test && npm run build
npm audit --audit-level=moderate
```

### Playwright
`tests/ui/live.spec.ts` (live incidents at `/live/`) and `tests/ui/devices.spec.ts` (device overview at `/`) run against the built UI served by `vite preview` (Playwright `webServer` builds it), with the JSON API mocked through `page.route` and the incident WebSocket through `page.routeWebSocket` (`tests/ui/mocks.ts`), so no backend is needed: `cd tests/ui && npm ci && npx playwright test live.spec.ts devices.spec.ts --project=chromium`.

---

## Critical Gotchas

### Source-assertion tests
Many tests read source, manifest or doc files as text and assert on their contents (`test_*_sources.py`). When renaming config keys, doc sections, script flags or manifest fields, grep `tests/` for the old string.

### `ENABLE_APPLY=false` by default in gateway-agent
The Wi-Fi AP will not start unless `ENABLE_APPLY=true`, which the `k8s/overlays/gateway-prod` overlay sets.

### All K8s pods require resource limits
Cluster policy enforces limits. Do not create or update manifests or chart templates without `resources.limits`.

### ARM64 only
Every image and binary must run on `linux/arm64`.

### Gateway node
The gateway node carries the AP and the sensor only; streaming workloads must not schedule there.
```bash
kubectl label node <gateway-node> node-role.kubernetes.io/gateway=true
```

### Secrets
Chart values reference Secrets by name; never put credentials in values files.

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

- Branch naming: `<type>/<short-description>`, e.g. `feat/flink-port-scan-rule`
- Commit message types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`
- Prefer focused commits with clear intent; avoid mixing unrelated changes in one commit
- Push every session — don't accumulate local-only commits
- Prefer opening a PR with a concise title and summary; call out test coverage and documentation or workflow updates
