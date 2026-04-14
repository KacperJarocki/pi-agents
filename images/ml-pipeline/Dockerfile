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

# Basic import smoke-test (fails build on broken entrypoints)
RUN python -c "from app.train import train_model; from app.inference import run_inference_loop" >/dev/null

RUN useradd -m -u 1000 mluser && \
    chown -R mluser:mluser /app

USER mluser

# Default to inference loop; K8s overrides for training CronJob.
CMD ["python", "-m", "app.inference"]
