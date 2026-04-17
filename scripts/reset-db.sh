#!/usr/bin/env bash
# reset-db.sh — wipe or truncate the IoT Security SQLite database on the cluster
# Usage:
#   ./reset-db.sh --full        drop entire DB file + restart all pods
#   ./reset-db.sh --data-only   DELETE all time-series rows, keep schema + devices
set -euo pipefail

NS="iot-security"
DB_PATH="/data/iot-security.db"

PODS_TO_RESTART=(
  "deployment/collector"
  "deployment/gateway-api"
  "deployment/ml-inference"
  "deployment/dashboard"
)

usage() {
  printf 'Usage: %s [--full | --data-only]\n' "$0" >&2
  printf '\n  --full       Remove the DB file entirely and restart all pods\n' >&2
  printf '  --data-only  DELETE traffic/anomaly rows, keep schema and devices table\n' >&2
  exit 1
}

[[ $# -eq 0 ]] && usage
MODE="$1"
[[ "$MODE" != "--full" && "$MODE" != "--data-only" ]] && usage

need_cmd() { command -v "$1" >/dev/null 2>&1 || { printf 'Missing: %s\n' "$1" >&2; exit 1; }; }
need_cmd kubectl
need_cmd sqlite3

# Find a running pod that has the PVC mounted (gateway-api owns the PVC)
find_pod() {
  kubectl get pods -n "$NS" -l app=gateway-api \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null
}

POD=$(find_pod)
if [[ -z "$POD" ]]; then
  printf '[error] No running gateway-api pod found in namespace %s\n' "$NS" >&2
  exit 1
fi
printf '[info] Using pod: %s\n' "$POD"

# Safety prompt
if [[ "$MODE" == "--full" ]]; then
  printf '[WARN] This will DELETE the entire database file at %s and restart all pods.\n' "$DB_PATH"
else
  printf '[WARN] This will DELETE all rows from traffic_flows, anomalies, device_inference_history,\n'
  printf '       device_behavior_alerts, and device_flow_summaries. Schema and devices table are kept.\n'
fi
printf 'Namespace: %s   Pod: %s\n' "$NS" "$POD"
printf 'Type YES to continue: '
read -r confirm
[[ "$confirm" != "YES" ]] && { printf 'Aborted.\n'; exit 0; }

if [[ "$MODE" == "--full" ]]; then
  printf '[reset] Removing %s\n' "$DB_PATH"
  kubectl exec -n "$NS" "$POD" -- rm -f "$DB_PATH" "${DB_PATH}-wal" "${DB_PATH}-shm"
  printf '[reset] DB file removed\n'
else
  printf '[reset] Truncating time-series tables (keeping schema + devices)\n'
  kubectl exec -n "$NS" "$POD" -- sqlite3 "$DB_PATH" \
    "DELETE FROM traffic_flows;
     DELETE FROM anomalies;
     DELETE FROM device_inference_history;
     DELETE FROM device_behavior_alerts;
     DELETE FROM device_flow_summaries;
     VACUUM;"
  printf '[reset] Tables cleared and VACUUM done\n'
fi

printf '[restart] Rolling out all workloads...\n'
for dep in "${PODS_TO_RESTART[@]}"; do
  printf '  -> kubectl rollout restart %s -n %s\n' "$dep" "$NS"
  kubectl rollout restart "$dep" -n "$NS" || printf '  [warn] %s not found, skipping\n' "$dep"
done

printf '[restart] Waiting for gateway-api rollout...\n'
kubectl rollout status deployment/gateway-api -n "$NS" --timeout=120s

printf '\nDone. DB reset complete.\n'
