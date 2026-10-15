"""Projects traffic.detections.v2 (and traffic.health.v2) into PostgreSQL.

For every transition: one database transaction (dedupe, revision-guarded
upsert, feed row, NOTIFY), then the Kafka offset commit. A crash between the
two replays the message, which the transition table turns into a no-op.

Environment:
  KAFKA_BOOTSTRAP     Kafka bootstrap servers (default pi-agents-kafka-bootstrap:9092)
  DATABASE_URL        PostgreSQL connection URI (required)
  DETECTIONS_TOPIC    default traffic.detections.v2
  HEALTH_TOPIC        default traffic.health.v2 (latest per gateway/component/kind)
  GROUP_ID            default incident-projector
  HEARTBEAT_FILE      rewritten every loop iteration for the liveness probe
"""

from __future__ import annotations

import logging
import os
import pathlib
import signal
import threading
import time
from typing import Callable, Protocol

import psycopg
from google.protobuf.message import DecodeError

from app import db
from app.events_pb2 import DetectionTransition, HealthEvent

HEALTH_TOPIC = os.environ.get("HEALTH_TOPIC", "traffic.health.v2")

log = logging.getLogger("incident-projector")


class Message(Protocol):
    def value(self) -> bytes | None: ...
    def error(self): ...
    def topic(self) -> str: ...
    def partition(self) -> int: ...
    def offset(self) -> int: ...


class Consumer(Protocol):
    def poll(self, timeout: float) -> Message | None: ...
    def commit(self, message: Message, asynchronous: bool) -> None: ...
    def close(self) -> None: ...


class Projector:
    def __init__(
        self,
        consumer: Consumer,
        connect: Callable[[], psycopg.Connection],
        *,
        prune_every_s: float = 3600,
        clock: Callable[[], float] = time.monotonic,
        heartbeat: pathlib.Path | None = None,
    ):
        self.consumer = consumer
        self.connect = connect
        self.conn: psycopg.Connection | None = None
        self.prune_every_s = prune_every_s
        self.clock = clock
        self.last_prune = clock()
        self.heartbeat = heartbeat
        self.stats = {"applied": 0, "duplicates": 0, "malformed": 0, "db_retries": 0, "health": 0}

    def _db(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed:
            self.conn = self.connect()
        return self.conn

    def _health(self, msg: Message) -> None:
        try:
            event = HealthEvent.FromString(msg.value() or b"")
        except DecodeError:
            event = None
        if event is None or not event.envelope.gateway_id:
            self.stats["malformed"] += 1
            return
        self._with_retry(lambda conn: db.apply_health(conn, event))
        self.stats["health"] += 1

    def _with_retry(self, operation):
        """Runs a database operation, reconnecting and retrying on connection loss."""
        delay = 0.5
        while True:
            try:
                return operation(self._db())
            except psycopg.OperationalError as err:
                # Connection lost (e.g. primary failover): reconnect and retry
                # the same transition; the offset is not committed meanwhile.
                self.stats["db_retries"] += 1
                log.warning("database unavailable (%s); retrying in %.1fs", err, delay)
                if self.conn is not None:
                    self.conn.close()
                self.conn = None
                time.sleep(delay)
                delay = min(delay * 2, 10)

    def apply(self, msg: Message) -> int | None:
        """Decodes and applies one message; retries database errors until it succeeds."""
        if msg.topic() == HEALTH_TOPIC:
            self._health(msg)
            return None
        try:
            transition = DetectionTransition.FromString(msg.value() or b"")
        except DecodeError:
            transition = None
        if transition is None or not transition.transition_id or not transition.incident_id:
            self.stats["malformed"] += 1
            log.warning(
                "skipping malformed transition at %s[%d]@%d", msg.topic(), msg.partition(), msg.offset()
            )
            return None
        cursor = self._with_retry(lambda conn: db.apply_transition(conn, transition))
        self.stats["applied" if cursor is not None else "duplicates"] += 1
        return cursor

    def handle(self, msg: Message) -> int | None:
        """Applies a message, then commits its offset (after the database commit)."""
        cursor = self.apply(msg)
        self.consumer.commit(message=msg, asynchronous=False)
        return cursor

    def tick(self) -> None:
        if self.clock() - self.last_prune >= self.prune_every_s:
            removed = db.prune_feed(self._db())
            log.info("pruned %d feed rows", removed)
            self.last_prune = self.clock()
        if self.heartbeat is not None:
            self.heartbeat.write_text(f"{time.time():.0f} {self.stats}\n")

    def run(self, stop: threading.Event) -> None:
        db.migrate(self._db())
        while not stop.is_set():
            msg = self.consumer.poll(1.0)
            if msg is not None:
                if msg.error():
                    log.warning("kafka error: %s", msg.error())
                else:
                    self.handle(msg)
            self.tick()
        self.consumer.close()


def main() -> None:  # pragma: no cover - wiring only
    from confluent_kafka import Consumer as KafkaConsumer

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dsn = os.environ["DATABASE_URL"]
    consumer = KafkaConsumer(
        {
            "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "pi-agents-kafka-bootstrap:9092"),
            "group.id": os.environ.get("GROUP_ID", "incident-projector"),
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            # The Flink sink is transactional; reading uncommitted keeps the
            # projector off the checkpoint interval. Duplicates from aborted
            # transactions are removed by the transition primary key.
            "isolation.level": "read_uncommitted",
        }
    )
    consumer.subscribe([os.environ.get("DETECTIONS_TOPIC", "traffic.detections.v2"), HEALTH_TOPIC])
    heartbeat = os.environ.get("HEARTBEAT_FILE")
    projector = Projector(
        consumer,
        lambda: psycopg.connect(dsn, autocommit=True),
        heartbeat=pathlib.Path(heartbeat) if heartbeat else None,
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    projector.run(stop)


if __name__ == "__main__":  # pragma: no cover
    main()
