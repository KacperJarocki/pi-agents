#!/usr/bin/env sh
set -eu

echo "[*] iot-ap starting hostapd + dnsmasq"

# dnsmasq leases w hostPath
mkdir -p /var/lib/dnsmasq

# hostapd w tle, dnsmasq na foreground (żeby pod żył)
hostapd -dd /config/hostapd.conf &
dnsmasq -k --conf-file=/config/dnsmasq.conf
