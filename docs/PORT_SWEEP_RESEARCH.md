# Port Sweep Research Script

`scripts/port-sweep.py` generates controlled TCP connect-sweep traffic from a device connected to the IoT Wi-Fi. It does not read the API or dashboard; use dashboard timestamps and model panels to measure false positives, false negatives, and reaction time.

By default, the main profiles run for a full inference bucket or longer. `positive` runs for 300 seconds, so the one-liner produces sustained sweep traffic instead of a short burst.

## One-Liner

```bash
./scripts/port-sweep.sh --target 192.168.100.1 --profile positive
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

Use `run.json`, `markers.jsonl`, and `summary.json` timestamps as the ground-truth experiment window when reading the dashboard. If the run is stopped with `Ctrl+C`, `summary.json` is still written with `interrupted: true`.

## Suggested Measurement Protocol

1. Confirm the tested device has trained models in the dashboard ML status view.
2. Run `negative` several times and record whether any model or heuristic flags the device.
3. Run `borderline` to document behavior near the `port_churn` threshold.
4. Run `positive` and record first visible dashboard reaction time.
5. Compare Isolation Forest, LOF, OCSVM, and Autoencoder scores for the same run window.
6. Repeat with `slow` and `aggressive` to test bucket sensitivity and saturation behavior.

Recommended fields to record from the dashboard: profile, run id, start timestamp, first alert timestamp, alert type, risk delta, model scores, and final TP/FP/FN/TN classification.
