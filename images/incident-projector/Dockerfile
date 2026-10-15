# Incident projector. Build from the repository root (it generates its
# Protobuf module from schemas/events/v2):
#   docker build -f images/incident-projector/Dockerfile .
FROM docker.io/library/python:3.12.11-slim-bookworm AS proto
WORKDIR /src
COPY images/incident-projector/requirements-build.txt .
RUN pip install --no-cache-dir -r requirements-build.txt
COPY schemas/events/v2 schemas/events/v2
COPY images/incident-projector/generate_proto.py .
RUN mkdir out && python generate_proto.py --schemas schemas/events/v2 --out out

FROM docker.io/library/python:3.12.11-slim-bookworm
WORKDIR /srv
COPY images/incident-projector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY images/incident-projector/app app
COPY --from=proto /src/out/events_pb2.py app/events_pb2.py
USER 65534
ENTRYPOINT ["python", "-m", "app.projector"]
