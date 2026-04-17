#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-burst}"
SECONDS_TOTAL="${SECONDS_TOTAL:-180}"
PARALLEL_DOWNLOADS="${PARALLEL_DOWNLOADS:-4}"
SLEEP_BETWEEN_WAVES="${SLEEP_BETWEEN_WAVES:-3}"

# Walidacja wartości numerycznych
if ! [[ "$SECONDS_TOTAL" =~ ^[0-9]+$ ]] || [[ "$SECONDS_TOTAL" -eq 0 ]]; then
  printf 'SECONDS_TOTAL must be a positive integer\n' >&2
  exit 1
fi
if ! [[ "$PARALLEL_DOWNLOADS" =~ ^[0-9]+$ ]] || [[ "$PARALLEL_DOWNLOADS" -eq 0 ]]; then
  printf 'PARALLEL_DOWNLOADS must be a positive integer\n' >&2
  exit 1
fi

DOWNLOAD_URLS=(
  "https://speed.hetzner.de/100MB.bin"
  "https://proof.ovh.net/files/100Mb.dat"
  "https://ipv4.download.thinkbroadband.com/100MB.zip"
)

DNS_HOSTS=(
  "google.com" "cloudflare.com" "github.com" "apple.com"
  "netflix.com" "youtube.com" "linkedin.com" "openai.com"
)

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  }
}
need_cmd curl

if command -v dig >/dev/null 2>&1; then
  DNS_CMD="dig"
  DNS_ARGS="+short"
elif command -v nslookup >/dev/null 2>&1; then
  DNS_CMD="nslookup"
  DNS_ARGS=""
else
  DNS_CMD=""
  DNS_ARGS=""
fi

# Portable cleanup: xargs -r nie istnieje na macOS
cleanup() {
  local pids
  pids=$(jobs -p)
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

run_dns_wave() {
  [[ -z "$DNS_CMD" ]] && return 0
  for host in "${DNS_HOSTS[@]}"; do
    # Bezpośrednie wywołanie zamiast sh -c (unikamy word splitting)
    "$DNS_CMD" $DNS_ARGS "$host" >/dev/null 2>&1 || true
  done
}

run_http_wave() {
  local workers=0
  local url_count=${#DOWNLOAD_URLS[@]}
  local idx=0

  while [[ $idx -lt $url_count ]]; do
    curl -L --fail --silent --output /dev/null \
      --range 0-52428799 "${DOWNLOAD_URLS[$idx]}" &
    workers=$((workers + 1))
    idx=$((idx + 1))

    if [[ "$workers" -ge "$PARALLEL_DOWNLOADS" ]]; then
      # wait -n wymaga bash 4.3+; fallback: czekamy na wszystkie
      if wait -n 2>/dev/null; then
        :
      else
        wait || true
        workers=0
        continue
      fi
      workers=$((workers - 1))
    fi
  done
  wait || true
}

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
  # Poprawka: zmienna pętli (nie "_", które nie jest bash-idiomem)
  for i in $(seq 1 "$PARALLEL_DOWNLOADS"); do
    curl -L --fail --silent --output /dev/null \
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

printf 'Mode=%s seconds=%s parallel=%s\n' "$MODE" "$SECONDS_TOTAL" "$PARALLEL_DOWNLOADS"
printf 'Use this from a laptop connected to the IoT Wi-Fi to generate visible traffic buckets and higher anomaly scores.\n'

case "$MODE" in
burst) run_burst ;;
spike) run_spike ;;
mix) run_mix ;;
*)
  printf 'Unknown mode: %s\n' "$MODE" >&2
  printf 'Usage: %s [burst|spike|mix]\n' "$0" >&2
  exit 1
  ;;
esac

printf 'Traffic generation complete. Wait 5-10 minutes for inference/anomaly cycles.\n'
