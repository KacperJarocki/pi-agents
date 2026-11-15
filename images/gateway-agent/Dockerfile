FROM docker.io/library/python:3.12.16-slim-bookworm

WORKDIR /app

# Tools needed for status/validate/apply on a gateway node.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        iproute2 \
        iptables \
        procps \
        hostapd \
        dnsmasq \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Basic import smoke-test
RUN python -c "from app.main import app" >/dev/null

# Run as root inside privileged pod (needed for iptables/hostapd).

EXPOSE 7000

# BIND_HOST limits the API to one address (the manifests pass the node IP, so
# it does not listen on the Wi-Fi interface or on other host addresses).
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"${BIND_HOST:-0.0.0.0}\" --port 7000"]
