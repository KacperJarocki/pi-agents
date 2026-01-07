#!/usr/bin/env sh
set -eu

IOT_IF="${IOT_IF:-wlan0}"
WAN_IF="${WAN_IF:-eth0}"
IOT_CIDR="${IOT_CIDR:-10.66.0.1/24}"

echo "[*] iot-gateway: IOT_IF=$IOT_IF WAN_IF=$WAN_IF IOT_CIDR=$IOT_CIDR"

echo "[*] enable IPv4 forwarding"
sysctl -w net.ipv4.ip_forward=1 >/dev/null

echo "[*] ensure IoT IP on $IOT_IF"
ip addr add "$IOT_CIDR" dev "$IOT_IF" 2>/dev/null || true
ip link set "$IOT_IF" up || true

echo "[*] apply nftables rules"
nft -f /config/nftables.conf

echo "[+] gateway ready"
nft list ruleset | head -n 80 || true

sleep infinity
