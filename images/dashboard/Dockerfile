FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Basic import smoke-test (fails build on broken entrypoints)
RUN python -c "from app.main import app" >/dev/null

RUN useradd -m -u 1000 dashuser && \
    chown -R dashuser:dashuser /app

USER dashuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
