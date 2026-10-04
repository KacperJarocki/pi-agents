# Capture Benchmark

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
