FROM debian:bookworm-slim

RUN apt-get update && \
  apt-get install -y --no-install-recommends \
  iproute2 iw rfkill ca-certificates && \
  rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY watchdog.sh /app/watchdog.sh
RUN chmod +x /app/watchdog.sh

ENTRYPOINT ["/app/watchdog.sh"]
