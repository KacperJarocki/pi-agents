# Gateway sensor: libpcap capture + librdkafka publishing (features live,kafka).
# Built natively per architecture (linux/arm64 for the cluster).
FROM docker.io/library/rust:1.94.0-slim-bookworm AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config libpcap-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked --features live,kafka \
 && strip target/release/pi-agents-sensor

FROM docker.io/library/debian:bookworm-20260918-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpcap0.8 zlib1g \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/pi-agents-sensor /usr/local/bin/pi-agents-sensor
ENTRYPOINT ["/usr/local/bin/pi-agents-sensor"]
