#!/usr/bin/env sh
set -eu

echo "[*] iot-ap starting hostapd + dnsmasq"

mkdir -p /var/lib/dnsmasq

hostapd -dd /config/hostapd.conf &
dnsmasq -k --conf-file=/config/dnsmasq.conf
