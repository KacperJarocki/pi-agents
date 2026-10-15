FROM docker.io/library/python:3.12.11-slim-bookworm
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
USER 65534
EXPOSE 8080
ENTRYPOINT ["python", "-m", "app.main"]
