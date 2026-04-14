FROM python:3.12-slim-bookworm

WORKDIR /app

# Tools needed for status/validate/apply on a gateway node.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        iproute2 \
        iptables \
        procps \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Basic import smoke-test
RUN python -c "from app.main import app" >/dev/null

RUN useradd -m -u 1000 agent && \
    chown -R agent:agent /app

USER agent

EXPOSE 7000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7000"]
