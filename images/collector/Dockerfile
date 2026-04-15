FROM python:3.12-slim-bookworm
RUN apt-get update && \
  apt-get install -y --no-install-recommends \
  tcpdump \
  iproute2 \
  procps \
  tshark \
  && rm -rf /var/lib/apt/lists/*

# ADD THIS LINE
RUN addgroup --system pcap && \
  adduser --system --ingroup pcap --no-create-home tcpdump

WORKDIR /app
RUN apt-get update && \
  apt-get install -y --no-install-recommends \
  gcc \
  libpcap-dev \
  && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
RUN python -c "import app; import app.collector; import app.database" >/dev/null
RUN chmod +x /app/app/entrypoint.sh
RUN useradd -m -u 1000 collector && \
  chown -R collector:collector /app
USER collector
ENTRYPOINT ["/app/app/entrypoint.sh"]
