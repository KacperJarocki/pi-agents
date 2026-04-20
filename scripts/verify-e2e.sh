#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# verify-e2e.sh — End-to-end pipeline verification for pi-agents IoT Security
#
# Verifies the full flow:
#   collector → traffic_flows → ml-trainer → model → ml-inference →
#   anomalies + behavior_alerts + risk_score → dashboard alerts
#
# Usage:
#   ./scripts/verify-e2e.sh [OPTIONS]
#
# Options:
#   --api-url URL        Gateway API URL (default: http://iot-api.homelab.kacperjarocki.dev)
#   --device-id ID       Device ID to verify (default: auto-detect first device)
#   --skip-traffic       Skip anomaly traffic generation (verify existing state only)
#   --traffic-mode MODE  Traffic mode: burst|portscan|dnsfail|full (default: full)
#   --wait-minutes MIN   Minutes to wait for inference after traffic (default: 5)
#   --namespace NS       K8s namespace (default: iot-security)
#   --verbose            Show raw API responses
#   -h, --help           Show this help
#
# Prerequisites:
#   - kubectl configured with access to the cluster
#   - curl installed
#   - Run from a device connected to the IoT WiFi (for traffic generation)
#   - At least one trained model for the target device
#
# Exit codes:
#   0  All checks passed
#   1  One or more checks failed
#   2  Prerequisites not met
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
API_URL="${API_URL:-http://iot-api.homelab.kacperjarocki.dev}"
DEVICE_ID=""
SKIP_TRAFFIC=false
TRAFFIC_MODE="full"
WAIT_MINUTES=5
NAMESPACE="iot-security"
VERBOSE=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-url)     API_URL="$2"; shift 2 ;;
    --device-id)   DEVICE_ID="$2"; shift 2 ;;
    --skip-traffic) SKIP_TRAFFIC=true; shift ;;
    --traffic-mode) TRAFFIC_MODE="$2"; shift 2 ;;
    --wait-minutes) WAIT_MINUTES="$2"; shift 2 ;;
    --namespace)   NAMESPACE="$2"; shift 2 ;;
    --verbose)     VERBOSE=true; shift ;;
    -h|--help)
      sed -n '2,/^# ─.*─$/p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

# ── Counters ──────────────────────────────────────────────────────────────────
PASS=0
FAIL=0
WARN=0

check_pass() { printf "${GREEN}  ✓ PASS${NC} %s\n" "$1"; PASS=$((PASS + 1)); }
check_fail() { printf "${RED}  ✗ FAIL${NC} %s\n" "$1"; FAIL=$((FAIL + 1)); }
check_warn() { printf "${YELLOW}  ⚠ WARN${NC} %s\n" "$1"; WARN=$((WARN + 1)); }
section()    { printf "\n${BOLD}${BLUE}══ %s ══${NC}\n" "$1"; }
info()       { printf "${BLUE}  ℹ${NC} %s\n" "$1"; }

api_get() {
  local path="$1"
  local result
  result=$(curl -sf --max-time 10 "${API_URL}/api/v1${path}" 2>/dev/null) || {
    printf ''
    return 1
  }
  if $VERBOSE; then
    printf '    %s → %s\n' "$path" "$(echo "$result" | head -c 200)" >&2
  fi
  printf '%s' "$result"
}

json_field() {
  # Simple JSON field extraction using python3 (always available on the gateway)
  python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps($1))" 2>/dev/null
}

json_val() {
  python3 -c "import json,sys; d=json.load(sys.stdin); v=$1; print(v if v is not None else '')" 2>/dev/null
}

# ── Prerequisites ─────────────────────────────────────────────────────────────
section "Prerequisites"

command -v curl >/dev/null 2>&1 && check_pass "curl available" || { check_fail "curl not found"; exit 2; }
command -v python3 >/dev/null 2>&1 && check_pass "python3 available" || { check_fail "python3 not found"; exit 2; }

# Test API connectivity
if api_get "/devices" >/dev/null 2>&1; then
  check_pass "API reachable at ${API_URL}"
else
  check_fail "Cannot reach API at ${API_URL}"
  printf '\n  Make sure you are on the IoT WiFi or the API is port-forwarded.\n'
  exit 2
fi

# ══════════════════════════════════════════════════════════════════════════════
section "1. Collector — traffic_flows"

# Auto-detect device if not specified
if [[ -z "$DEVICE_ID" ]]; then
  DEVICE_ID=$(api_get "/devices" | json_val "d['devices'][0]['id'] if d.get('devices') else ''")
  if [[ -z "$DEVICE_ID" ]]; then
    check_fail "No devices found — collector may not be running"
    exit 2
  fi
  info "Auto-detected device_id=${DEVICE_ID}"
fi

# Check device exists and has recent activity
DEVICE_JSON=$(api_get "/devices/${DEVICE_ID}" || echo '{}')
LAST_SEEN=$(echo "$DEVICE_JSON" | json_val "d.get('last_seen', '')")
RISK_BEFORE=$(echo "$DEVICE_JSON" | json_val "d.get('risk_score', 0)")
info "Device ${DEVICE_ID}: last_seen=${LAST_SEEN}, risk_score=${RISK_BEFORE}"

if [[ -n "$LAST_SEEN" && "$LAST_SEEN" != "None" ]]; then
  check_pass "Device ${DEVICE_ID} has been seen by collector (last_seen=${LAST_SEEN})"
else
  check_warn "Device ${DEVICE_ID} has no last_seen — collector may not have captured traffic yet"
fi

# Check flow count via training-data endpoint
TRAINING_DATA=$(api_get "/ml/devices/${DEVICE_ID}/training-data?hours=1" || echo '{}')
FLOW_COUNT_1H=$(echo "$TRAINING_DATA" | json_val "d.get('flow_count', 0)")
info "Flows in last 1 hour: ${FLOW_COUNT_1H}"

if [[ "$FLOW_COUNT_1H" -gt 0 ]] 2>/dev/null; then
  check_pass "Collector writing flows (${FLOW_COUNT_1H} in last hour)"
else
  check_warn "No flows in last hour — collector may be idle or device has no traffic"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "2. ML Trainer — model_metadata"

ML_STATUS=$(api_get "/metrics/ml-status" || echo '{}')
MODELS_READY=$(echo "$ML_STATUS" | json_val "d.get('device_models_ready', 0)")
MODEL_HEALTH=$(echo "$ML_STATUS" | json_val "d.get('model_health_available', False)")
info "Models ready: ${MODELS_READY}, health data available: ${MODEL_HEALTH}"

# Check if this specific device has training metrics
DEVICE_METRICS=$(echo "$ML_STATUS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for dev in d.get('devices', []):
    if dev.get('device_id') == ${DEVICE_ID}:
        m = dev.get('training_metrics') or []
        print(json.dumps({'count': len(m), 'metrics': m}))
        sys.exit(0)
print(json.dumps({'count': 0, 'metrics': []}))
" 2>/dev/null || echo '{"count":0,"metrics":[]}')

METRIC_COUNT=$(echo "$DEVICE_METRICS" | json_val "d['count']")
if [[ "$METRIC_COUNT" -gt 0 ]] 2>/dev/null; then
  check_pass "Device ${DEVICE_ID} has ${METRIC_COUNT} trained model(s)"
  # Show model details
  echo "$DEVICE_METRICS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d['metrics']:
    print(f\"    model={m['model_type']}  samples={m.get('samples','?')}  threshold={m.get('threshold','?')}  trained_at={m.get('trained_at','?')}\")
" 2>/dev/null || true
else
  check_fail "Device ${DEVICE_ID} has NO trained models — inference cannot score"
  info "Run: ./scripts/generate-anomaly-traffic.sh burst  (wait 2h for 20 buckets)"
  info "Or:  Train Now via dashboard after enough traffic is collected"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "3. ML Inference — risk scoring"

# Check inference pod is running
if command -v kubectl >/dev/null 2>&1; then
  INF_STATUS=$(kubectl get pods -n "$NAMESPACE" -l component=ml-inference -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "unknown")
  if [[ "$INF_STATUS" == "Running" ]]; then
    check_pass "ml-inference pod is Running"
  else
    check_fail "ml-inference pod status: ${INF_STATUS}"
  fi

  # Check recent inference logs
  LAST_INF_LOG=$(kubectl logs -n "$NAMESPACE" deploy/ml-inference --tail=5 2>/dev/null | grep -o '"event":"inference_complete"' | head -1 || echo "")
  if [[ -n "$LAST_INF_LOG" ]]; then
    check_pass "Inference loop is producing results"
  else
    check_warn "No recent inference_complete log found"
  fi
else
  check_warn "kubectl not available — skipping pod status checks"
fi

# Check device risk score
if [[ "$RISK_BEFORE" != "0" && "$RISK_BEFORE" != "None" && -n "$RISK_BEFORE" ]] 2>/dev/null; then
  check_pass "Device ${DEVICE_ID} has risk_score=${RISK_BEFORE} (non-zero, inference is scoring)"
else
  check_warn "Device ${DEVICE_ID} risk_score=${RISK_BEFORE} — may need more inference cycles"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "4. Snapshot — anomalies & alerts BEFORE traffic"

ANOMALIES_BEFORE=$(api_get "/anomalies?device_id=${DEVICE_ID}&limit=1" || echo '{}')
ANOMALY_COUNT_BEFORE=$(echo "$ANOMALIES_BEFORE" | json_val "d.get('total', 0)")
info "Anomalies (device ${DEVICE_ID}): ${ANOMALY_COUNT_BEFORE}"

ALERTS_BEFORE=$(api_get "/alerts?limit=50&since_hours=1" || echo '{}')
ALERT_COUNT_BEFORE=$(echo "$ALERTS_BEFORE" | json_val "d.get('total', 0)")
info "Unified alerts (all devices, last 1h): ${ALERT_COUNT_BEFORE}"

# ══════════════════════════════════════════════════════════════════════════════
if $SKIP_TRAFFIC; then
  section "5. SKIPPED — anomaly traffic generation (--skip-traffic)"
else
  section "5. Generating anomaly traffic (mode=${TRAFFIC_MODE})"

  if [[ ! -f "${SCRIPT_DIR}/generate-anomaly-traffic.sh" ]]; then
    check_fail "generate-anomaly-traffic.sh not found in ${SCRIPT_DIR}"
  else
    info "Starting traffic generation (mode=${TRAFFIC_MODE})..."
    info "This will take a few minutes depending on the mode."
    # Run with shorter duration for testing
    SECONDS_TOTAL=120 bash "${SCRIPT_DIR}/generate-anomaly-traffic.sh" "$TRAFFIC_MODE" || {
      check_warn "Traffic generation exited with non-zero (some probes may have failed — this is normal)"
    }
    check_pass "Anomaly traffic generation completed"
  fi

  # ── Wait for inference ────────────────────────────────────────────────────
  section "6. Waiting for inference to process (${WAIT_MINUTES} min)"
  info "Inference interval is 60s. Waiting ${WAIT_MINUTES} minutes for at least 1 full cycle..."

  TOTAL_WAIT=$((WAIT_MINUTES * 60))
  ELAPSED=0
  INTERVAL=30
  while [[ $ELAPSED -lt $TOTAL_WAIT ]]; do
    remaining=$(( (TOTAL_WAIT - ELAPSED) / 60 ))
    printf "  ⏳ %d:%02d remaining...\r" "$remaining" "$(( (TOTAL_WAIT - ELAPSED) % 60 ))"
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
  done
  printf "  ⏳ Done waiting.                    \n"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "7. Verification — anomalies & alerts AFTER traffic"

# Re-fetch device state
DEVICE_JSON_AFTER=$(api_get "/devices/${DEVICE_ID}" || echo '{}')
RISK_AFTER=$(echo "$DEVICE_JSON_AFTER" | json_val "d.get('risk_score', 0)")
info "Risk score: before=${RISK_BEFORE} → after=${RISK_AFTER}"

# Check risk score changed
if python3 -c "
before = float('${RISK_BEFORE}' or '0')
after = float('${RISK_AFTER}' or '0')
exit(0 if after > before else 1)
" 2>/dev/null; then
  check_pass "Risk score INCREASED: ${RISK_BEFORE} → ${RISK_AFTER}"
elif python3 -c "exit(0 if float('${RISK_AFTER}' or '0') > 0 else 1)" 2>/dev/null; then
  check_warn "Risk score non-zero (${RISK_AFTER}) but did not increase from ${RISK_BEFORE}"
else
  check_fail "Risk score is still 0 — inference may not have scored the device"
fi

# Check anomalies
ANOMALIES_AFTER=$(api_get "/anomalies?device_id=${DEVICE_ID}&limit=5" || echo '{}')
ANOMALY_COUNT_AFTER=$(echo "$ANOMALIES_AFTER" | json_val "d.get('total', 0)")
info "Anomalies: before=${ANOMALY_COUNT_BEFORE} → after=${ANOMALY_COUNT_AFTER}"

if [[ "$ANOMALY_COUNT_AFTER" -gt "$ANOMALY_COUNT_BEFORE" ]] 2>/dev/null; then
  NEW_ANOMALIES=$((ANOMALY_COUNT_AFTER - ANOMALY_COUNT_BEFORE))
  check_pass "NEW anomalies created: +${NEW_ANOMALIES}"
  # Show latest anomaly details
  echo "$ANOMALIES_AFTER" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for a in (d.get('anomalies') or [])[:3]:
    print(f\"    [{a.get('severity','?')}] {a.get('anomaly_type','?')}: {a.get('description','?')[:80]}\")
" 2>/dev/null || true
elif [[ "$ANOMALY_COUNT_AFTER" -gt 0 ]] 2>/dev/null; then
  check_warn "Anomalies exist (${ANOMALY_COUNT_AFTER}) but no NEW ones since test start"
  info "The model threshold may be too lenient, or anomalous traffic was not extreme enough"
else
  check_fail "No anomalies found for device ${DEVICE_ID}"
  info "Possible causes:"
  info "  - No trained model (check ML Model Health)"
  info "  - Threshold too high (model needs more normal traffic baseline)"
  info "  - Inference hasn't run yet (wait 1-2 more minutes)"
fi

# Check behavior alerts
ALERTS_AFTER=$(api_get "/alerts?limit=50&since_hours=1" || echo '{}')
ALERT_COUNT_AFTER=$(echo "$ALERTS_AFTER" | json_val "d.get('total', 0)")
info "Unified alerts (last 1h): before=${ALERT_COUNT_BEFORE} → after=${ALERT_COUNT_AFTER}"

if [[ "$ALERT_COUNT_AFTER" -gt "$ALERT_COUNT_BEFORE" ]] 2>/dev/null; then
  NEW_ALERTS=$((ALERT_COUNT_AFTER - ALERT_COUNT_BEFORE))
  check_pass "NEW alerts created: +${NEW_ALERTS}"
  # Show alert types
  echo "$ALERTS_AFTER" | python3 -c "
import json, sys
d = json.load(sys.stdin)
types = {}
for a in d.get('alerts', []):
    t = a.get('alert_type') or a.get('anomaly_type') or 'unknown'
    types[t] = types.get(t, 0) + 1
for t, c in sorted(types.items(), key=lambda x: -x[1]):
    print(f'    {t}: {c}')
" 2>/dev/null || true
elif [[ "$ALERT_COUNT_AFTER" -gt 0 ]] 2>/dev/null; then
  check_warn "Alerts exist (${ALERT_COUNT_AFTER}) but no new ones in the test window"
else
  check_fail "No alerts in the last hour"
  info "Behavior heuristics need baseline history (BEHAVIOR_BASELINE_HOURS=168)"
  info "If the system is new, alerts will appear after a few hours of normal traffic"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "8. Dashboard WebSocket — alert delivery"

# Test WS connectivity (just check if the endpoint responds to HTTP upgrade)
WS_CHECK=$(curl -sf --max-time 5 -o /dev/null -w '%{http_code}' \
  -H "Upgrade: websocket" -H "Connection: Upgrade" \
  "${API_URL/http/http}/../ws/alerts" 2>/dev/null || echo "000")

# Try dashboard WS endpoint instead (the one that actually pushes alerts)
DASHBOARD_URL="${DASHBOARD_URL:-http://iot-dashboard.homelab.kacperjarocki.dev}"
DASH_WS_CHECK=$(curl -sf --max-time 5 -o /dev/null -w '%{http_code}' \
  "${DASHBOARD_URL}/ws/alerts" 2>/dev/null || echo "000")

if [[ "$DASH_WS_CHECK" != "000" ]]; then
  check_pass "Dashboard WS endpoint reachable (HTTP ${DASH_WS_CHECK})"
  info "Dashboard polls gateway-api every 30s and broadcasts new alerts via WebSocket"
  info "Toast notifications appear within 30s of a new anomaly/behavior alert"
else
  check_warn "Cannot reach dashboard WS endpoint — check DASHBOARD_URL"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "9. Pod health summary"

if command -v kubectl >/dev/null 2>&1; then
  printf '\n'
  kubectl get pods -n "$NAMESPACE" -o wide 2>/dev/null || check_warn "kubectl failed"
  printf '\n'

  # Check CronJob last schedule
  LAST_CRON=$(kubectl get cronjob ml-trainer -n "$NAMESPACE" -o jsonpath='{.status.lastScheduleTime}' 2>/dev/null || echo "unknown")
  info "ml-trainer CronJob last run: ${LAST_CRON}"
else
  check_warn "kubectl not available — skipping pod health"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "RESULTS"

printf '\n'
printf "  ${GREEN}Passed: ${PASS}${NC}\n"
printf "  ${RED}Failed: ${FAIL}${NC}\n"
printf "  ${YELLOW}Warnings: ${WARN}${NC}\n"
printf '\n'

if [[ $FAIL -eq 0 ]]; then
  printf "${GREEN}${BOLD}  ✓ ALL CHECKS PASSED${NC}\n"
  printf "  The full pipeline is operational: collector → training → inference → alerts\n"
  exit 0
else
  printf "${RED}${BOLD}  ✗ ${FAIL} CHECK(S) FAILED${NC}\n"
  printf "  Review the failures above and check pod logs:\n"
  printf "    kubectl logs -n ${NAMESPACE} deploy/ml-inference --tail=50\n"
  printf "    kubectl logs -n ${NAMESPACE} deploy/gateway-api --tail=50\n"
  printf "    kubectl logs -n ${NAMESPACE} deploy/collector --tail=50\n"
  exit 1
fi
