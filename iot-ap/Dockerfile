FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
  hostapd dnsmasq iproute2 procps ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
