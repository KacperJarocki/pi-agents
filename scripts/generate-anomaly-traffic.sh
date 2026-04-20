#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-burst}"
SECONDS_TOTAL="${SECONDS_TOTAL:-180}"
PARALLEL_DOWNLOADS="${PARALLEL_DOWNLOADS:-4}"
SLEEP_BETWEEN_WAVES="${SLEEP_BETWEEN_WAVES:-3}"
API_URL="${API_URL:-http://iot-api.homelab.kacperjarocki.dev}"

# Subnet prefix for ICMP sweep (last octet will be iterated)
SWEEP_PREFIX="${SWEEP_PREFIX:-192.168.100}"

# Numerics validation
for var in SECONDS_TOTAL PARALLEL_DOWNLOADS; do
  val="${!var}"
  if ! [[ "$val" =~ ^[0-9]+$ ]] || [[ "$val" -eq 0 ]]; then
    printf '%s must be a positive integer\n' "$var" >&2; exit 1
  fi
done

DOWNLOAD_URLS=(
  "https://speed.hetzner.de/100MB.bin"
  "https://proof.ovh.net/files/100Mb.dat"
  "https://ipv4.download.thinkbroadband.com/100MB.zip"
)

DNS_HOSTS=(
  "google.com" "cloudflare.com" "github.com" "apple.com"
  "netflix.com" "youtube.com" "linkedin.com" "openai.com"
)

# Domains that reliably NXDOMAIN
NXDOMAIN_HOSTS=(
  "this-domain-does-not-exist-xyz123.com"
  "nonexistent-iot-test-abc987.net"
  "fake-c2-server-noresolve.io"
  "totally-bogus-domain-qwerty456.org"
)

# Novel external destinations never seen in normal traffic
NOVEL_HOSTS=(
  "rare-endpoint-1.example.com"
  "novel-dest-2.example.com"
  "unknown-cdn-3.example.com"
  "fresh-target-4.example.com"
  "untested-host-5.example.com"
  "new-server-6.example.com"
)

# Ports to hit for port churn detection
CHURN_PORTS=(22 23 25 80 443 8080 8443 3389 5900 6379 27017 5432 3306 1433 9200 11211)

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { printf 'Missing required command: %s\n' "$1" >&2; exit 1; }
}
need_cmd curl

if command -v dig >/dev/null 2>&1; then
  DNS_CMD="dig"; DNS_ARGS="+short +time=2 +tries=1"
elif command -v nslookup >/dev/null 2>&1; then
  DNS_CMD="nslookup"; DNS_ARGS=""
else
  DNS_CMD=""; DNS_ARGS=""
fi

cleanup() {
  local pids; pids=$(jobs -p)
  [[ -n "$pids" ]] && kill $pids >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# ─── helpers ──────────────────────────────────────────────────────────────────

check_ml_status() {
  # When called from verify-e2e.sh on the AP network, the API is unreachable.
  # SKIP_ML_CHECK=1 suppresses the API call.
  if [[ "${SKIP_ML_CHECK:-0}" == "1" ]]; then
    printf '[info] Skipping ML status check (SKIP_ML_CHECK=1)\n'
    return
  fi
  local status
  status=$(curl -sf --max-time 5 "${API_URL}/api/v1/metrics/ml-status" 2>/dev/null || true)
  if [[ -z "$status" ]]; then
    printf '[warn] Cannot reach %s — ensure you are on the IoT Wi-Fi and API is up\n' "$API_URL" >&2
    return
  fi
  local ready
  ready=$(printf '%s' "$status" | grep -o '"models_ready":[0-9]*' | grep -o '[0-9]*' || echo "0")
  if [[ "$ready" -eq 0 ]]; then
    printf '[warn] ML has 0 trained models — anomaly alerts require ~20 traffic buckets (~2h) per device.\n' >&2
    printf '       Run burst/mix mode first and wait for ml-trainer CronJob to complete.\n' >&2
  else
    printf '[info] ML status: %s trained models ready\n' "$ready"
  fi
}

run_dns_wave() {
  [[ -z "$DNS_CMD" ]] && return 0
  for host in "${DNS_HOSTS[@]}"; do
    "$DNS_CMD" $DNS_ARGS "$host" >/dev/null 2>&1 || true
  done
}

run_http_wave() {
  local workers=0 idx=0 url_count=${#DOWNLOAD_URLS[@]}
  while [[ $idx -lt $url_count ]]; do
    curl -L --fail --silent --output /dev/null --max-time 30 \
      --range 0-52428799 "${DOWNLOAD_URLS[$idx]}" &
    workers=$((workers + 1)); idx=$((idx + 1))
    if [[ "$workers" -ge "$PARALLEL_DOWNLOADS" ]]; then
      wait -n 2>/dev/null || { wait || true; workers=0; continue; }
      workers=$((workers - 1))
    fi
  done
  wait || true
}

# ─── modes ────────────────────────────────────────────────────────────────────

run_burst() {
  local end_at=$((SECONDS + SECONDS_TOTAL))
  while [[ $SECONDS -lt $end_at ]]; do
    printf '[burst] wave at %s\n' "$(date '+%H:%M:%S')"
    run_dns_wave
    run_http_wave
    sleep "$SLEEP_BETWEEN_WAVES"
  done
}

run_spike() {
  printf '[spike] starting %s parallel download workers\n' "$PARALLEL_DOWNLOADS"
  for i in $(seq 1 "$PARALLEL_DOWNLOADS"); do
    curl -L --fail --silent --output /dev/null --max-time 60 \
      --range 0-104857599 "${DOWNLOAD_URLS[0]}" &
  done
  wait || true
}

run_mix() {
  local end_at=$((SECONDS + SECONDS_TOTAL))
  while [[ $SECONDS -lt $end_at ]]; do
    printf '[mix] dns + burst + idle jitter at %s\n' "$(date '+%H:%M:%S')"
    run_dns_wave
    run_http_wave &
    sleep 1
    run_dns_wave
    wait || true
    sleep "$SLEEP_BETWEEN_WAVES"
  done
}

# Triggers: port_churn (≥6 unique ports or ≥5 never-seen ports)
run_portscan() {
  need_cmd nc
  printf '[portscan] probing %s ports on gateway to trigger port_churn\n' "${#CHURN_PORTS[@]}"
  local target="${SWEEP_PREFIX}.1"
  for port in "${CHURN_PORTS[@]}"; do
    # nc with 1s timeout; ignore errors — we just want the flow recorded
    nc -z -w1 "$target" "$port" >/dev/null 2>&1 || true
    printf '  -> %s:%s\n' "$target" "$port"
    sleep 0.3
  done
  printf '[portscan] done. Unique ports hit: %s\n' "${#CHURN_PORTS[@]}"
}

# Triggers: dns_failure_spike + dns_nxdomain_burst
run_dnsfail() {
  [[ -z "$DNS_CMD" ]] && { printf '[dnsfail] no dig/nslookup found, skipping\n' >&2; return; }
  local end_at=$((SECONDS + SECONDS_TOTAL))
  printf '[dnsfail] sending NXDOMAIN queries to trigger dns_failure_spike / dns_nxdomain_burst\n'
  while [[ $SECONDS -lt $end_at ]]; do
    for host in "${NXDOMAIN_HOSTS[@]}"; do
      "$DNS_CMD" $DNS_ARGS "$host" >/dev/null 2>&1 || true
      printf '  -> NXDOMAIN: %s\n' "$host"
    done
    sleep 2
  done
}

# Triggers: icmp_sweep_suspected + icmp_echo_fanout (≥4 ICMP to ≥3 different IPs)
run_icmpsweep() {
  need_cmd ping
  local count=0
  printf '[icmpsweep] pinging %s.1-%s.20 to trigger icmp_sweep / icmp_echo_fanout\n' \
    "$SWEEP_PREFIX" "$SWEEP_PREFIX"
  for octet in $(seq 1 20); do
    local target="${SWEEP_PREFIX}.${octet}"
    ping -c1 -W1 "$target" >/dev/null 2>&1 || true
    printf '  -> ping %s\n' "$target"
    count=$((count + 1))
    sleep 0.2
  done
  printf '[icmpsweep] sent %s ICMP echo requests\n' "$count"
}

# Triggers: beaconing_suspected (regular small packets to same IP, stdev/median ≤0.35)
# Runs for BEACON_DURATION seconds (default 10 min), interval BEACON_INTERVAL (default 30s)
run_beacon() {
  local target="${BEACON_TARGET:-https://speed.hetzner.de/1KB.bin}"
  local interval="${BEACON_INTERVAL:-30}"
  local duration="${BEACON_DURATION:-600}"
  local end_at=$((SECONDS + duration))
  printf '[beacon] beaconing to %s every %ss for %ss — triggers beaconing_suspected\n' \
    "$target" "$interval" "$duration"
  while [[ $SECONDS -lt $end_at ]]; do
    curl -sf --max-time 5 --output /dev/null "$target" >/dev/null 2>&1 || true
    printf '  -> beacon at %s\n' "$(date '+%H:%M:%S')"
    sleep "$interval"
  done
}

# Triggers: destination_novelty (≥2 new IPs, ≥40% destinations novel)
run_novelty() {
  printf '[novelty] hitting novel destinations to trigger destination_novelty\n'
  for host in "${NOVEL_HOSTS[@]}"; do
    curl -sf --max-time 5 --output /dev/null "https://${host}/" >/dev/null 2>&1 || true
    printf '  -> %s\n' "$host"
    sleep 0.5
  done
  # Also try raw IPs from uncommon ranges (public, non-CDN)
  local novel_ips=("45.79.3.1" "139.162.10.1" "178.62.5.1" "104.131.1.1" "159.65.1.1")
  for ip in "${novel_ips[@]}"; do
    curl -sf --max-time 3 --output /dev/null "http://${ip}/" >/dev/null 2>&1 || true
    printf '  -> raw IP: %s\n' "$ip"
    sleep 0.5
  done
}

# Runs all modes sequentially
run_full() {
  printf '[full] running all anomaly modes sequentially\n'
  printf '=== 1/6 burst (traffic_pattern_drift + dns_burst) ===\n'
  SECONDS_TOTAL=120 run_burst
  printf '=== 2/6 portscan (port_churn) ===\n'
  run_portscan
  printf '=== 3/6 dnsfail (dns_failure_spike + dns_nxdomain_burst) ===\n'
  SECONDS_TOTAL=60 run_dnsfail
  printf '=== 4/6 icmpsweep (icmp_sweep_suspected + icmp_echo_fanout) ===\n'
  run_icmpsweep
  printf '=== 5/6 novelty (destination_novelty) ===\n'
  run_novelty
  printf '=== 6/6 beacon (beaconing_suspected) — shortened to 3min ===\n'
  BEACON_DURATION=180 run_beacon
  printf '[full] All anomaly traffic complete.\n'
}

# ─── main ─────────────────────────────────────────────────────────────────────

printf 'Mode=%s seconds=%s parallel=%s\n' "$MODE" "$SECONDS_TOTAL" "$PARALLEL_DOWNLOADS"
printf 'Run this from a device connected to the IoT Wi-Fi so traffic flows through the gateway.\n'
check_ml_status

case "$MODE" in
  burst)     run_burst ;;
  spike)     run_spike ;;
  mix)       run_mix ;;
  portscan)  run_portscan ;;
  dnsfail)   run_dnsfail ;;
  icmpsweep) run_icmpsweep ;;
  beacon)    run_beacon ;;
  novelty)   run_novelty ;;
  full)      run_full ;;
  *)
    printf 'Unknown mode: %s\n' "$MODE" >&2
    printf 'Usage: %s [burst|spike|mix|portscan|dnsfail|icmpsweep|beacon|novelty|full]\n' "$0" >&2
    printf '\n  burst      - bulk HTTP + DNS waves  (traffic_pattern_drift, dns_burst)\n' >&2
    printf '  spike      - max parallel downloads  (traffic_pattern_drift)\n' >&2
    printf '  mix        - interleaved HTTP + DNS  (traffic_pattern_drift, dns_burst)\n' >&2
    printf '  portscan   - multi-port TCP probes   (port_churn)\n' >&2
    printf '  dnsfail    - NXDOMAIN queries        (dns_failure_spike, dns_nxdomain_burst)\n' >&2
    printf '  icmpsweep  - ICMP to subnet range    (icmp_sweep_suspected, icmp_echo_fanout)\n' >&2
    printf '  beacon     - regular small requests  (beaconing_suspected)\n' >&2
    printf '  novelty    - new IPs/domains         (destination_novelty)\n' >&2
    printf '  full       - all of the above        (all alert types)\n' >&2
    exit 1
    ;;
esac

printf 'Traffic generation complete. Wait 5-10 minutes for inference/anomaly cycles.\n'
