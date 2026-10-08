# Capture Benchmark

## Current latency sources (configuration, not measurements)

The existing capture path launches `tcpdump` for up to `CAPTURE_TIMEOUT`,
parses its completed PCAP with `tshark`, buffers the parsed packets, and
flushes them to SQLite every `FLUSH_INTERVAL`. On the gateway Kubernetes
deployment both timeouts are **2 seconds**; in local Compose they are **5
seconds**. A full packet-count capture may finish earlier, and parser/SQLite
time adds to these values. The collector currently timestamps a parsed flow
when it processes the PCAP, rather than taking the timestamp from the packet.

Inference currently considers only **closed 5-minute buckets** with a
**30-second grace period**. Its loop sleeps **60 seconds after each cycle**
in Kubernetes or **300 seconds** in local Compose; each cycle also reads up
to 24 hours of flows and may take nonzero processing time. The dashboard
polls for alert changes every **30 seconds** before sending WebSocket toasts;
HTMX alert fragments also refresh every 30 seconds. Cached API reads can add
their own TTL. These values are from `k8s/gateway/collector-deployment.yaml`,
`k8s/gateway/ml-inference-deployment.yaml`, `docker-compose.yml`,
`images/collector/app/collector.py`, `images/ml-pipeline/app/inference.py`
and `images/dashboard/app/main.py`; **they are not end-to-end latency
percentiles**. Reducing just one interval does not remove the closed-bucket
and polling waits.

The streaming design bypasses these waits by using packet-time events,
continuously updated sliding windows and push notifications. No live hardware
benchmark is required to begin its implementation; the target of under 2
seconds is not claimed as verified until it is measured separately.

## Optional hardware baseline

Run this on each Raspberry Pi gateway after installing the collector image:

```sh
python3 scripts/benchmark_capture.py \
  --interface wlan0 \
  --packet-count 10000 \
  --duration-seconds 30 \
  --json /tmp/capture-benchmark.json
```

The command needs capture privileges and `tcpdump`/`tshark`. It records:

- capture return code and stderr,
- capture duration,
- PCAP size,
- parsed packet count,
- parser duration,
- packets per second.

Run the same workload three times per node and report median and p95. Keep the
JSON files with the hardware model, kernel, image digest, CPU governor, NVMe
model, and interface configuration. The benchmark is a baseline only; it is
not the target performance of the future Rust sensor.

For CI or a machine without capture privileges:

```sh
python3 scripts/benchmark_capture.py --dry-run
```
