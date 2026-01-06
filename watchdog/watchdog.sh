#!/usr/bin/env sh
set -eu

IFACE="${IFACE:-wlan0}"
CHANNEL="${CHANNEL:-6}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-10}"
WAIT_SECONDS="${WAIT_SECONDS:-120}"

echo "[*] Wi-Fi monitor watchdog starting: iface=$IFACE channel=$CHANNEL interval=${INTERVAL_SECONDS}s"

i=0
while [ "$i" -lt "$WAIT_SECONDS" ]; do
  [ -e "/sys/class/net/$IFACE" ] && echo "[*] $IFACE present" && break
  i=$((i + 1))
  echo "[!] waiting for $IFACE..."
  sleep 1
done

ensure_monitor() {
  if ! ip link show "$IFACE" >/dev/null 2>&1; then
    echo "[!] $IFACE not available"
    return 0
  fi

  INFO="$(iw dev "$IFACE" info 2>/dev/null || true)"
  echo "$INFO" | grep -q "type monitor" && return 0

  echo "[!] $IFACE is NOT in monitor mode -> fixing"
  ip link set "$IFACE" down || true
  iw dev "$IFACE" set type monitor
  ip link set "$IFACE" up
  iw dev "$IFACE" set channel "$CHANNEL" || true

  echo "[+] fixed:"
  iw dev "$IFACE" info || true
}

while true; do
  ensure_monitor || true
  sleep "$INTERVAL_SECONDS"
done
