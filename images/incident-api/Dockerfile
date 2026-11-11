# The UI is plain static output, so it builds once on the build platform
# (no QEMU) and is copied into the arm64/amd64 runtime image.
FROM --platform=$BUILDPLATFORM docker.io/library/node:26.13.0-bookworm-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

FROM docker.io/library/python:3.15.0-slim-bookworm
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY --from=ui /ui/dist app/static
USER 65534
EXPOSE 8080
ENTRYPOINT ["python", "-m", "app.main"]
