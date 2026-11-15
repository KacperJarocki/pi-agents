"""Projects traffic.detections.v2 (and traffic.health.v2) into PostgreSQL.

For every transition: one database transaction (dedupe, revision-guarded
upsert, feed row, NOTIFY), then the Kafka offset commit. A crash between the
two replays the message, which the transition table turns into a no-op.

Feature updates (traffic.features.v2) are far more frequent and only feed the
device overview, so they have a consumer of their own: a batch is reduced to
the newest window per device, horizon and minute, written in one transaction
and committed once. Detections never wait behind a backlog of features.

Environment:
  KAFKA_BOOTSTRAP     Kafka bootstrap servers (default pi-agents-kafka-bootstrap:9092)
  DATABASE_URL        PostgreSQL connection URI (required)
  DETECTIONS_TOPIC    default traffic.detections.v2
  HEALTH_TOPIC        default traffic.health.v2 (latest per gateway/component/kind)
  FEATURES_TOPIC      default traffic.features.v2 (latest view per device/horizon)
  GROUP_ID            default incident-projector
  FEATURES_GROUP_ID   default <GROUP_ID>-features
  FEATURE_BATCH       feature updates per database transaction (default 500)
  HEARTBEAT_FILE      rewritten every loop iteration for the liveness probe
"""

from __future__ import annotations

import logging
import os
import pathlib
import signal
import threading
import time
from collections.abc import Callable
from typing import Protocol

import psycopg
from google.protobuf.message import DecodeError

from app import db
from app.events_pb2 import DetectionTransition, FeatureUpdate, HealthEvent

HEALTH_TOPIC = os.environ.get("HEALTH_TOPIC", "traffic.health.v2")
FEATURES_TOPIC = os.environ.get("FEATURES_TOPIC", "traffic.features.v2")

log = logging.getLogger("incident-projector")


class Message(Protocol):
    def value(self) -> bytes | None: ...
    def error(self): ...
    def topic(self) -> str: ...
    def partition(self) -> int: ...
    def offset(self) -> int: ...


class Consumer(Protocol):
    def poll(self, timeout: float) -> Message | None: ...
    def consume(self, num_messages: int, timeout: float) -> list[Message]: ...
    def commit(self, message: Message | None = None, asynchronous: bool = True) -> None: ...
    def close(self) -> None: ...


class Projector:
    def __init__(
        self,
        consumer: Consumer,
        connect: Callable[[], psycopg.Connection],
        *,
        features_consumer: Consumer | None = None,
        feature_batch: int = 500,
        prune_every_s: float = 3600,
        clock: Callable[[], float] = time.monotonic,
        heartbeat: pathlib.Path | None = None,
    ):
        self.consumer = consumer
        # Without one, feature updates are expected on `consumer` and applied singly.
        self.features_consumer = features_consumer
        self.feature_batch = feature_batch
        self.last_heartbeat = 0.0
        self.connect = connect
        self.conn: psycopg.Connection | None = None
        self.prune_every_s = prune_every_s
        self.clock = clock
        self.last_prune = clock()
        self.heartbeat = heartbeat
        self.stats = {
            "applied": 0,
            "duplicates": 0,
            "malformed": 0,
            "db_retries": 0,
            "health": 0,
            "features": 0,
            "feature_rows": 0,
        }

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

    def _features(self, msg: Message) -> None:
        try:
            update = FeatureUpdate.FromString(msg.value() or b"")
        except DecodeError:
            update = None
        if update is None or not update.envelope.device_key or not update.window_end_unix_millis:
            self.stats["malformed"] += 1
            return
        self._with_retry(lambda conn: db.apply_features(conn, update))
        self.stats["features"] += 1

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
        if msg.topic() == FEATURES_TOPIC:
            self._features(msg)
            return None
        try:
            transition = DetectionTransition.FromString(msg.value() or b"")
        except DecodeError:
            transition = None
        if transition is None or not transition.transition_id or not transition.incident_id:
            self.stats["malformed"] += 1
            log.warning("skipping malformed transition at %s[%d]@%d", msg.topic(), msg.partition(), msg.offset())
            return None
        cursor = self._with_retry(lambda conn: db.apply_transition(conn, transition))
        self.stats["applied" if cursor is not None else "duplicates"] += 1
        return cursor

    def handle_features(self, timeout: float = 0.0) -> int:
        """Applies the feature updates that are waiting; returns how many were read.

        Only the newest window per device, horizon and minute can change a
        row, so the rest of the batch is dropped before touching the database.
        """
        if self.features_consumer is None:
            return 0
        messages = self.features_consumer.consume(self.feature_batch, timeout)
        newest: dict[tuple[str, int, int], FeatureUpdate] = {}
        read = 0
        for msg in messages:
            if msg.error():
                log.warning("kafka error: %s", msg.error())
                continue
            read += 1
            try:
                update = FeatureUpdate.FromString(msg.value() or b"")
            except DecodeError:
                update = None
            if update is None or not update.envelope.device_key or not update.window_end_unix_millis:
                self.stats["malformed"] += 1
                continue
            self.stats["features"] += 1
            key = (update.envelope.device_key, update.horizon_seconds, update.window_end_unix_millis // 60_000)
            kept = newest.get(key)
            if kept is None or kept.window_end_unix_millis <= update.window_end_unix_millis:
                newest[key] = update
        if newest:

            def write(conn: psycopg.Connection) -> None:
                with conn.transaction():
                    for update in newest.values():
                        db.apply_features(conn, update)

            self._with_retry(write)
            self.stats["feature_rows"] += len(newest)
        if read:
            # Positions of everything consumed above, in one commit.
            self.features_consumer.commit(asynchronous=False)
        return read

    def handle(self, msg: Message) -> int | None:
        """Applies a message, then commits its offset (after the database commit)."""
        cursor = self.apply(msg)
        self.consumer.commit(message=msg, asynchronous=False)
        return cursor

    def tick(self) -> None:
        if self.clock() - self.last_prune >= self.prune_every_s:
            removed = db.prune_feed(self._db())
            history = db.prune_feature_history(self._db())
            log.info("pruned %d feed rows and %d device history rows", removed, history)
            self.last_prune = self.clock()
        if self.heartbeat is not None and self.clock() - self.last_heartbeat >= 1:
            self.last_heartbeat = self.clock()
            self.heartbeat.write_text(f"{time.time():.0f} {self.stats}\n")

    def run(self, stop: threading.Event) -> None:
        db.migrate(self._db())
        # With a features consumer the loop alternates between the two, so it
        # must not sit in one poll while the other topic has work.
        wait = 1.0 if self.features_consumer is None else 0.2
        while not stop.is_set():
            msg = self.consumer.poll(wait)
            if msg is not None:
                if msg.error():
                    log.warning("kafka error: %s", msg.error())
                else:
                    self.handle(msg)
            self.handle_features()
            self.tick()
        self.consumer.close()
        if self.features_consumer is not None:
            self.features_consumer.close()


def main() -> None:  # pragma: no cover - wiring only
    from confluent_kafka import Consumer as KafkaConsumer

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dsn = os.environ["DATABASE_URL"]
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "pi-agents-kafka-bootstrap:9092")
    group = os.environ.get("GROUP_ID", "incident-projector")
    consumer = KafkaConsumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": group,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            # The Flink detections sink is at-least-once; duplicates are
            # removed by the transition primary key.
            "isolation.level": "read_uncommitted",
        }
    )
    consumer.subscribe([os.environ.get("DETECTIONS_TOPIC", "traffic.detections.v2"), HEALTH_TOPIC])
    features_consumer = KafkaConsumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": os.environ.get("FEATURES_GROUP_ID", f"{group}-features"),
            "enable.auto.commit": False,
            # Views are latest-wins: a group without offsets (the first start
            # after this consumer was split off) skips the retained backlog.
            "auto.offset.reset": "latest",
        }
    )
    features_consumer.subscribe([FEATURES_TOPIC])
    heartbeat = os.environ.get("HEARTBEAT_FILE")
    projector = Projector(
        consumer,
        lambda: psycopg.connect(dsn, autocommit=True),
        features_consumer=features_consumer,
        feature_batch=int(os.environ.get("FEATURE_BATCH", "500")),
        heartbeat=pathlib.Path(heartbeat) if heartbeat else None,
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    projector.run(stop)


if __name__ == "__main__":  # pragma: no cover
    main()
