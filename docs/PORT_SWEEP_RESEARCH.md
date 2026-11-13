# Port Sweep Research Script

`scripts/port-sweep.py` generates controlled TCP connect-sweep traffic from a device connected to the IoT Wi-Fi. It does not read the API or dashboard; use dashboard timestamps and model panels to measure false positives, false negatives, and reaction time.

By default, the main profiles run for a full inference bucket or longer. `positive` runs for 300 seconds, so the one-liner produces sustained sweep traffic instead of a short burst.

## One-Liner

```bash
./scripts/port-sweep.sh --target 192.168.50.1 --profile positive
```

For a complete port-sweep research run instead of manually launching each profile, keep benign IoT traffic running in the background and run:

```bash
python3 research.py
```

This does not require gateway API access. By default it runs local subnet discovery (`--discover-subnet auto`) and starts the 35-phase `balanced35` plan in the background with a 5-minute gap, randomized probe order, and shuffled phase order.

Run it from the device being evaluated, not from the gateway host, so the collector attributes traffic to the tested device.

## Profiles

### Vertical port sweeps

| Profile | Purpose | Expected Detection |
|---------|---------|--------------------|
| `negative` | Low-volume normal web-like probes for FP checks | No `port_churn` |
| `borderline` | Five ports near the heuristic threshold | Boundary behavior |
| `positive` | Broad diverse admin/db/IoT ports | `port_churn` likely + strong ML port-diversity signal |
| `slow` | Positive port set spread over time | Bucket/reaction-time sensitivity |
| `aggressive` | Broad high-rate sweep | Strong heuristic/ML response |

The `positive`, `slow`, and `aggressive` profiles are designed around the current `port_churn` heuristic: at least 6 unique destination ports and at least 5 new destination ports in the latest inference bucket.

### Other attack shapes

The sweeps above are all **vertical** (many ports on a host). These profiles cover
different attack geometries so the dataset is not just port sweeps. Each run records
its `attack_shape` and `expected_signal` in `run.json`, so you can use them as
ground-truth labels. `expected_signal` is what the **current** streaming port-scan
rule (distinct failed targets: 60 s ≥ 20 or 900 s ≥ 40) is expected to do.

| Profile | Shape | Attack it mimics | `expected_signal` |
|---------|-------|------------------|-------------------|
| `horizontal` | horizontal | One service port (Telnet 23) across many hosts — worm/Mirai propagation | `port_churn` (each refusing host is a distinct failed target) |
| `telnet-brute` | brute | Mirai-style Telnet/SSH credential brute on a few hosts | `below_threshold_gap` |
| `web-fuzz` | brute | Directory/endpoint fuzzing against one host's web ports | `below_threshold_gap` |
| `rdp-brute` | brute | Remote-access (RDP/VNC/SSH) credential brute | `below_threshold_gap` |
| `flood` | flood | High-rate connection burst to one target port (load/DoS signature) | `below_threshold_gap` |
| `beacon` | beacon | Low-and-slow C2 beacon / exfil staging to one destination | `below_threshold_gap` |

`port_churn` means the current rule should open an incident. `below_threshold_gap`
marks a real attack the current rule does **not** catch, because it concentrates on
one or a few targets instead of fanning out: these are kept deliberately as detection
coverage gaps, useful for arguing which rules (volume, beaconing, brute-force) the
system still needs. `horizontal` needs several discovered hosts — run it with subnet
discovery (the default in `research.py`), not a single `--target`.

`flood` is a high connection **rate** to one port to produce a load signature for
research on your own testbed; it is not a spoofed or amplified denial-of-service.

```bash
# Horizontal Telnet sweep across the discovered /24
./scripts/port-sweep.sh --discover-subnet auto --profile horizontal

# Telnet credential brute against the gateway (a below-threshold volume anomaly)
./scripts/port-sweep.sh --target 192.168.50.1 --profile telnet-brute

# Low-and-slow beacon shape
./scripts/port-sweep.sh --target 192.168.50.1 --profile beacon --dry-run
```

`research.py` runs the suggested port-sweep protocol as one experiment: `negative`, `borderline`, `positive`, `slow`, and `aggressive`, with a quiet gap between phases. It stores top-level timestamps under `artifacts/research-runs/<run-id>/` so dashboard readings can be mapped back to each phase. Add `--phases normal,negative,borderline,positive,slow,aggressive` only if you want the benign IoT emulator included in the same run.

`python3 research.py` is intentionally the ready-to-run overnight command. It is equivalent to:

```bash
python3 research.py --preset balanced35 --gap 5m --randomize --shuffle-phases --detach
```

`balanced35` expands to 35 phases: 10 `negative`, 10 `positive`, 5 `borderline`, 5 `slow`, and 5 `aggressive`. With the default profile durations and a 5-minute gap it takes about 6h35m. Discovery is still enabled by default, so this command finds reachable hosts in the local `/24` before each port-sweep phase.

### The `attackmix` preset

For a dataset that spans every attack shape rather than just vertical sweeps, use the
`attackmix` preset. It runs one balanced pass of the sweeps plus the horizontal scan,
the brute-force shapes, the flood, and the beacon (23 phases), interleaved so no two
identical phases run back to back:

```bash
python3 research.py --preset attackmix --gap 5m --randomize --shuffle-phases --detach
```

With default durations and a 5-minute gap it takes about 2h30m before gaps. Each phase
still writes its own child output and the `attack_shape` / `expected_signal` labels, so
the run covers both detections the rule should make (`horizontal`) and the gaps it
should miss (`telnet-brute`, `web-fuzz`, `rdp-brute`, `flood`, `beacon`). You can also
pick shapes directly with `--phases`, e.g. `--phases horizontal,telnet-brute,flood`.

Target discovery options:

| Option | Use case |
|--------|----------|
| `python3 research.py` | Auto-discover reachable hosts in the local `/24` without API access |
| `python3 research.py --discover-subnet 192.168.100.0/24` | Explicit subnet discovery |
| `python3 research.py --no-discover --target 192.168.50.1` | Single target fallback to the gateway AP IP, useful when client isolation blocks device-to-device probes |
| `python3 research.py --targets-file targets.txt` | Predefined target list |
| `python3 research.py --targets-api ...` | API-discovered targets when API is reachable |

## Useful Commands

```bash
# Check the planned traffic without sending packets
./scripts/port-sweep.sh --profile positive --dry-run

# Reproducible positive run with deterministic jitter/order
./scripts/port-sweep.sh --target 192.168.50.1 --profile positive --randomize --seed 42

# False-positive baseline
./scripts/port-sweep.sh --target 192.168.50.1 --profile negative

# Main positive test
./scripts/port-sweep.sh --target 192.168.50.1 --profile positive

# Ten-minute positive test
./scripts/port-sweep.sh --target 192.168.50.1 --profile positive --duration 10m

# Slower sweep for reaction-time and bucket sensitivity checks
./scripts/port-sweep.sh --target 192.168.50.1 --profile slow

# Stronger stress case
./scripts/port-sweep.sh --target 192.168.50.1 --profile aggressive --repeat 2 --randomize

# Sweep every active device known by the gateway API
./scripts/port-sweep.sh --targets-api http://localhost:8080/api/v1/devices --api-active-only --profile aggressive --repeat 2 --randomize

# Overnight balanced research dataset with auto local subnet discovery
python3 research.py

# Same plan spelled out explicitly
python3 research.py --preset balanced35 --gap 5m --randomize --shuffle-phases --detach

# Follow detached run logs after noting the run id printed by the command
tail -f artifacts/research-runs/<run-id>/research.log

# Full port-sweep research protocol with explicit subnet discovery
python3 research.py --discover-subnet 192.168.100.0/24 --randomize --seed 42

# Fallback when client isolation blocks device discovery
python3 research.py --no-discover --target 192.168.50.1 --randomize --seed 42

# Short smoke run for checking the protocol wiring
python3 research.py --phases negative,positive --sweep-duration 30s --gap 10s --dry-run

# Custom ports
./scripts/port-sweep.sh --target 192.168.50.1 --ports 22,23,80,443,3389,5900,6379,27017

# One-shot legacy behavior, useful only for quick smoke checks
./scripts/port-sweep.sh --target 192.168.50.1 --profile positive --duration 0 --repeat 1
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
  status.json
  summary.json
```

Detached runs additionally write:

```text
artifacts/research-runs/<run-id>/
  pid
  research.log
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
