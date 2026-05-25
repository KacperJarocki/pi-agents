# Port Sweep Research Script

`scripts/port-sweep.py` generates controlled TCP connect-sweep traffic from a device connected to the IoT Wi-Fi. It does not read the API or dashboard; use dashboard timestamps and model panels to measure false positives, false negatives, and reaction time.

By default, the main profiles run for a full inference bucket or longer. `positive` runs for 300 seconds, so the one-liner produces sustained sweep traffic instead of a short burst.

## One-Liner

```bash
./scripts/port-sweep.sh --target 192.168.100.1 --profile positive
```

For a complete port-sweep research run instead of manually launching each profile, keep benign IoT traffic running in the background and run:

```bash
python3 research.py
```

This does not require gateway API access. By default it runs local subnet discovery (`--discover-subnet auto`) and then sweeps the discovered reachable hosts.

Run it from the device being evaluated, not from the gateway host, so the collector attributes traffic to the tested device.

## Profiles

| Profile | Purpose | Expected Detection |
|---------|---------|--------------------|
| `negative` | Low-volume normal web-like probes for FP checks | No `port_churn` |
| `borderline` | Five ports near the heuristic threshold | Boundary behavior |
| `positive` | Sixteen diverse ports | `port_churn` likely |
| `slow` | Positive port set spread over time | Bucket/reaction-time sensitivity |
| `aggressive` | Broad high-rate sweep | Strong heuristic/ML response |

The `positive`, `slow`, and `aggressive` profiles are designed around the current `port_churn` heuristic: at least 6 unique destination ports and at least 5 new destination ports in the latest inference bucket.

`research.py` runs the suggested port-sweep protocol as one experiment: `negative`, `borderline`, `positive`, `slow`, and `aggressive`, with a quiet gap between phases. It stores top-level timestamps under `artifacts/research-runs/<run-id>/` so dashboard readings can be mapped back to each phase. Add `--phases normal,negative,borderline,positive,slow,aggressive` only if you want the benign IoT emulator included in the same run.

Target discovery options:

| Option | Use case |
|--------|----------|
| `python3 research.py` | Auto-discover reachable hosts in the local `/24` without API access |
| `python3 research.py --discover-subnet 192.168.100.0/24` | Explicit subnet discovery |
| `python3 research.py --no-discover --target 192.168.100.1` | Single target fallback, useful when client isolation blocks device-to-device probes |
| `python3 research.py --targets-file targets.txt` | Predefined target list |
| `python3 research.py --targets-api ...` | API-discovered targets when API is reachable |

## Useful Commands

```bash
# Check the planned traffic without sending packets
./scripts/port-sweep.sh --profile positive --dry-run

# Reproducible positive run with deterministic jitter/order
./scripts/port-sweep.sh --target 192.168.100.1 --profile positive --randomize --seed 42

# False-positive baseline
./scripts/port-sweep.sh --target 192.168.100.1 --profile negative

# Main positive test
./scripts/port-sweep.sh --target 192.168.100.1 --profile positive

# Ten-minute positive test
./scripts/port-sweep.sh --target 192.168.100.1 --profile positive --duration 10m

# Slower sweep for reaction-time and bucket sensitivity checks
./scripts/port-sweep.sh --target 192.168.100.1 --profile slow

# Stronger stress case
./scripts/port-sweep.sh --target 192.168.100.1 --profile aggressive --repeat 2 --randomize

# Sweep every active device known by the gateway API
./scripts/port-sweep.sh --targets-api http://localhost:8080/api/v1/devices --api-active-only --profile aggressive --repeat 2 --randomize

# Full port-sweep research protocol with auto local subnet discovery
python3 research.py

# Full port-sweep research protocol with explicit subnet discovery
python3 research.py --discover-subnet 192.168.100.0/24 --randomize --seed 42

# Fallback when client isolation blocks device discovery
python3 research.py --no-discover --target 192.168.100.1 --randomize --seed 42

# Short smoke run for checking the protocol wiring
python3 research.py --phases negative,positive --sweep-duration 30s --gap 10s --dry-run

# Custom ports
./scripts/port-sweep.sh --target 192.168.100.1 --ports 22,23,80,443,3389,5900,6379,27017

# One-shot legacy behavior, useful only for quick smoke checks
./scripts/port-sweep.sh --target 192.168.100.1 --profile positive --duration 0 --repeat 1
```

## Output

Every run writes local metadata under:

```text
artifacts/port-sweep/<run-id>/
  run.json
  markers.jsonl
  probes.jsonl
  summary.json
```

Full protocol runs write top-level metadata under:

```text
artifacts/research-runs/<run-id>/
  manifest.json
  markers.jsonl
  summary.json
```

Child phase outputs go to `artifacts/port-sweep/<run-id>-01-negative/`, `artifacts/port-sweep/<run-id>-02-borderline/`, etc. If the optional `normal` phase is included, its child output goes to `artifacts/iot-emulator/<run-id>-01-normal/`.

The runner also prints a final k6-like terminal summary with pass rate, local start/end times, duration, and a compact timeline bar per phase. The same fields are saved in `summary.json` as `started_at_local`, `ended_at_local`, `duration_seconds`, and `duration_human`.

Use `run.json`, `markers.jsonl`, and `summary.json` timestamps as the ground-truth experiment window when reading the dashboard. If the run is stopped with `Ctrl+C`, `summary.json` is still written with `interrupted: true`.

## Suggested Measurement Protocol

1. Confirm the tested device has trained models in the dashboard ML status view.
2. Keep benign IoT traffic running in the background.
3. Run `python3 research.py ...` from the device being evaluated.
4. Use `artifacts/research-runs/<run-id>/markers.jsonl` to split dashboard readings by phase.
5. Record whether background normal traffic and the `negative` phase produce false positives.
6. Record first visible dashboard reaction time for `positive`, `slow`, and `aggressive`.
7. Compare Isolation Forest, LOF, OCSVM, and Autoencoder scores for the same phase windows.

Recommended fields to record from the dashboard: profile, run id, start timestamp, first alert timestamp, alert type, risk delta, model scores, and final TP/FP/FN/TN classification.
