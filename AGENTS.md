# pi-agents

Real-time IoT threat detection on a K3s cluster of 5× Raspberry Pi 5 (8 GB, NVMe). The gateway Pi is the Wi-Fi AP and capture point; a Rust sensor publishes traffic events to Kafka, Flink evaluates per-device sliding windows, and incidents are pushed to the browser.

The thesis-era batch pipeline (collector → SQLite → ml-trainer/ml-inference → gateway-api → dashboard) has been removed. Its last state is the git tag `thesis-final`; check it out for anything thesis-related.

---

## Where things are

```
images/gateway-agent/   # Wi-Fi AP controller (hostapd/dnsmasq), FastAPI on port 7000
sensors/rust-sensor/    # Rust capture sensor (Cargo crate)
schemas/events/         # Protobuf event contracts; v2 is current, v1 frozen
charts/pi-agents/       # Helm chart: streaming config, Strimzi Kafka, Flink
k8s/base/               # Namespace + streaming ConfigMap
k8s/gateway/            # gateway-agent Deployment/Service
k8s/overlays/gateway-prod/  # Sets ENABLE_APPLY=true (activates the AP)
k8s/flux/               # Flux sources + HelmReleases (operators, then the chart)
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
kubectl kustomize k8s/flux
```

### Local run
Only `gateway-agent` is in `docker-compose.yml`, behind the `gateway` profile (Linux only: host network + privileged). Locally only `podman` is installed, not docker.

**Podman machine clock drift**: the VM clock can lag the host, which breaks `apt-get update` with "Release file is not valid yet". Fix:
```bash
CORRECT_TIME=$(date -u +"%Y-%m-%d %H:%M:%S") && podman machine ssh "sudo date -s '$CORRECT_TIME UTC'"
```

### CI
- `validate.yml`: `compileall images`, unit tests, Helm lint/template of both Flink profiles, `kubectl kustomize k8s/gateway` + client dry run.
- `rust-sensor.yml`: fmt, test and an aarch64 check for the sensor.
- `streaming-contracts.yml`: event contract validator and its tests.
- `docker-build.yml`: builds `images/*` on PRs (amd64, no push) and on `main` (amd64 + arm64, push to `ghcr.io`, tags `latest` and `sha-<8 chars>`).

### Playwright
`tests/ui/` keeps the Playwright harness (`playwright.config.ts`, `package.json`, `BASE_URL` env) for the upcoming `/live` incidents page. There are no specs until that page exists.

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
