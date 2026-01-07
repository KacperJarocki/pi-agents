FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
  iproute2 nftables procps ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
