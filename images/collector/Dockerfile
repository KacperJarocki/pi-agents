FROM python:3.12-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tcpdump \
        iproute2 \
        procps \
        tshark \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpcap-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN useradd -m -u 1000 collector && \
    chown -R collector:collector /app

USER collector

ENTRYPOINT ["/app/app/entrypoint.sh"]
