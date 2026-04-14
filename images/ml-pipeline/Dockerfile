FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/

RUN useradd -m -u 1000 mluser && \
    chown -R mluser:mluser /app

USER mluser

CMD ["python", "/app/main.py"]
