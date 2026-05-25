# Port Sweep Research Script

`scripts/port-sweep.py` generates controlled TCP connect-sweep traffic from a device connected to the IoT Wi-Fi. It does not read the API or dashboard; use dashboard timestamps and model panels to measure false positives, false negatives, and reaction time.

By default, the main profiles run for a full inference bucket or longer. `positive` runs for 300 seconds, so the one-liner produces sustained sweep traffic instead of a short burst.

## One-Liner

```bash
./scripts/port-sweep.sh --target 192.168.100.1 --profile positive
```

For a complete research run instead of manually launching each profile:

```bash
./scripts/research-traffic-runner.sh --targets-api http://localhost:8080/api/v1/devices --api-active-only --randomize --seed 42
```

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

`scripts/research-traffic-runner.py` runs the suggested protocol as one experiment: `normal` benign IoT baseline, then `negative`, `borderline`, `positive`, `slow`, and `aggressive`, with a quiet gap between phases. It stores top-level timestamps under `artifacts/research-runs/<run-id>/` so dashboard readings can be mapped back to each phase.

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

# Full research protocol with reproducible phase seeds
./scripts/research-traffic-runner.sh --targets-api http://localhost:8080/api/v1/devices --api-active-only --randomize --seed 42

# Short smoke run for checking the protocol wiring
./scripts/research-traffic-runner.sh --phases normal,negative,positive --normal-duration 60s --gap 10s --dry-run

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

Child phase outputs still go to `artifacts/iot-emulator/<run-id>-01-normal/` and `artifacts/port-sweep/<run-id>-02-negative/`, etc.

Use `run.json`, `markers.jsonl`, and `summary.json` timestamps as the ground-truth experiment window when reading the dashboard. If the run is stopped with `Ctrl+C`, `summary.json` is still written with `interrupted: true`.

## Suggested Measurement Protocol

1. Confirm the tested device has trained models in the dashboard ML status view.
2. Run `./scripts/research-traffic-runner.sh ...` from the device being evaluated.
3. Use `artifacts/research-runs/<run-id>/markers.jsonl` to split dashboard readings by phase.
4. Record whether `normal` and `negative` produce false positives.
5. Record first visible dashboard reaction time for `positive`, `slow`, and `aggressive`.
6. Compare Isolation Forest, LOF, OCSVM, and Autoencoder scores for the same phase windows.

Recommended fields to record from the dashboard: profile, run id, start timestamp, first alert timestamp, alert type, risk delta, model scores, and final TP/FP/FN/TN classification.
