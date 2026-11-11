"""Copies the last hours of traffic.flows.v2 from the prod Kafka to the dev Kafka.

Runs once a day as a CronJob in the dev namespace so dev has real traffic to
test the detection job, projector and UI against:

    python -m app.replay --source-bootstrap pi-agents-kafka-bootstrap.iot-security.svc:9092 \\
        --target-bootstrap pi-agents-kafka-bootstrap:9092 --window-hours 24

Records are copied byte for byte: key (the device key), value, timestamp and
headers stay the same; a ``pi-agents-replayed-from`` header is added. The
original event times need no rewrite: the window ends at the start of the run,
so consecutive daily runs cover adjacent windows and every device sees its
events in order (one device key maps to one partition on both sides). The
detection rule's device clock is event time plus wall time since the last
event, so a fast replay keeps hold and cooldown meaningful.

The source is only read: partitions are assigned by hand and offsets are never
committed, so the replay leaves no consumer group behind on prod.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

TOPIC = "traffic.flows.v2"
GROUP_ID = "pi-agents-dev-flow-replay"
REPLAY_HEADER = "pi-agents-replayed-from"

log = logging.getLogger("flow-replay")


@dataclass(frozen=True)
class Range:
    """Offsets [start, end) of one source partition to copy."""

    partition: int
    start: int
    end: int

    @property
    def count(self) -> int:
        return self.end - self.start


def plan(start_offsets: dict[int, int], end_offsets: dict[int, int]) -> list[Range]:
    """Offset ranges per partition.

    ``start_offsets`` comes from offsets_for_times (-1 when no record is at or
    after the window start), ``end_offsets`` is the high watermark taken when
    the run started. Partitions with nothing to copy are left out.
    """
    ranges = []
    for partition in sorted(end_offsets):
        start = start_offsets.get(partition, -1)
        end = end_offsets[partition]
        if start < 0 or start >= end:
            continue
        ranges.append(Range(partition, start, end))
    return ranges


class RateLimiter:
    """Caps records per second; 0 disables the cap."""

    def __init__(self, rate: float, clock=time.monotonic, sleep=time.sleep):
        self.rate = rate
        self.clock = clock
        self.sleep = sleep
        self.started = None
        self.sent = 0

    def acquire(self) -> None:
        if self.rate <= 0:
            return
        now = self.clock()
        if self.started is None:
            self.started = now
        due = self.started + self.sent / self.rate
        if due > now:
            self.sleep(due - now)
        self.sent += 1


@dataclass
class Stats:
    planned: int = 0
    copied: int = 0
    bytes: int = 0
    delivery_errors: int = 0
    partitions: dict[int, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "planned": self.planned,
            "copied": self.copied,
            "bytes": self.bytes,
            "delivery_errors": self.delivery_errors,
            "partitions": {str(p): n for p, n in sorted(self.partitions.items())},
        }


def copy(
    consumer,
    producer,
    ranges: list[Range],
    *,
    source_topic: str,
    target_topic: str,
    source_label: str,
    limiter: RateLimiter,
    max_messages: int = 0,
    idle_timeout: float = 60.0,
    make_partition=None,
    clock=time.monotonic,
) -> Stats:
    """Copies ``ranges`` from ``consumer`` to ``producer`` and flushes.

    ``consumer`` and ``producer`` follow the confluent_kafka interfaces; the
    consumer must already be assigned to the range starts. Stops at the planned
    end offsets, after ``max_messages`` records (0 = no cap), or raises when no
    record arrives for ``idle_timeout`` seconds while ranges are unfinished.
    """
    stats = Stats(planned=sum(r.count for r in ranges))
    if max_messages:
        stats.planned = min(stats.planned, max_messages)
    ends = {r.partition: r.end for r in ranges}
    remaining = {r.partition for r in ranges}

    def on_delivery(err, _msg):
        if err is not None:
            stats.delivery_errors += 1
            if stats.delivery_errors <= 10:
                log.error("delivery failed: %s", err)

    last_progress = clock()
    while remaining and not (max_messages and stats.copied >= max_messages):
        messages = consumer.consume(500, 1.0)
        if not messages:
            if clock() - last_progress > idle_timeout:
                raise RuntimeError(f"no records for {idle_timeout:.0f}s; unfinished partitions {sorted(remaining)}")
            continue
        last_progress = clock()
        for msg in messages:
            if msg.error() is not None:
                raise RuntimeError(f"consume failed: {msg.error()}")
            partition = msg.partition()
            if partition not in remaining:
                continue
            if msg.offset() >= ends[partition]:
                remaining.discard(partition)
                if make_partition is not None:
                    consumer.pause([make_partition(source_topic, partition)])
                continue
            limiter.acquire()
            headers = list(msg.headers() or [])
            headers.append((REPLAY_HEADER, source_label.encode()))
            _ts_type, ts = msg.timestamp()
            value = msg.value()
            while True:
                try:
                    producer.produce(
                        target_topic,
                        value=value,
                        key=msg.key(),
                        timestamp=ts if ts and ts > 0 else 0,
                        headers=headers,
                        on_delivery=on_delivery,
                    )
                    break
                except BufferError:
                    producer.poll(0.5)
            producer.poll(0)
            stats.copied += 1
            stats.bytes += len(value or b"")
            stats.partitions[partition] = stats.partitions.get(partition, 0) + 1
            if msg.offset() >= ends[partition] - 1:
                remaining.discard(partition)
                if make_partition is not None:
                    consumer.pause([make_partition(source_topic, partition)])
            if max_messages and stats.copied >= max_messages:
                break
            if stats.copied % 50_000 == 0:
                log.info("copied %d/%d records", stats.copied, stats.planned)

    left = producer.flush(120)
    if left:
        stats.delivery_errors += left
        log.error("%d records still undelivered after flush", left)
    return stats


def parse_args(argv=None) -> argparse.Namespace:
    env = os.environ.get
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source-bootstrap", default=env("SOURCE_BOOTSTRAP"))
    p.add_argument("--target-bootstrap", default=env("TARGET_BOOTSTRAP"))
    p.add_argument("--source-topic", default=env("SOURCE_TOPIC", TOPIC))
    p.add_argument("--target-topic", default=env("TARGET_TOPIC", TOPIC))
    p.add_argument("--source-label", default=env("SOURCE_LABEL", "prod"))
    p.add_argument("--window-hours", type=float, default=float(env("WINDOW_HOURS", "24")))
    p.add_argument(
        "--max-rate",
        type=float,
        default=float(env("MAX_RATE", "2000")),
        help="records per second, 0 = unlimited",
    )
    p.add_argument(
        "--max-messages",
        type=int,
        default=int(env("MAX_MESSAGES", "0")),
        help="stop after this many records, 0 = whole window",
    )
    args = p.parse_args(argv)
    if not args.source_bootstrap or not args.target_bootstrap:
        p.error("--source-bootstrap and --target-bootstrap (or SOURCE_/TARGET_BOOTSTRAP) are required")
    if args.source_bootstrap == args.target_bootstrap and args.source_topic == args.target_topic:
        p.error("source and target are the same topic on the same cluster")
    if args.window_hours <= 0:
        p.error("--window-hours must be positive")
    return args


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    from confluent_kafka import Consumer, Producer, TopicPartition

    run_start_ms = int(time.time() * 1000)
    window_start_ms = run_start_ms - int(args.window_hours * 3_600_000)

    consumer = Consumer(
        {
            "bootstrap.servers": args.source_bootstrap,
            "group.id": GROUP_ID,
            "client.id": "pi-agents-flow-replay",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "isolation.level": "read_committed",
        }
    )
    producer = Producer(
        {
            "bootstrap.servers": args.target_bootstrap,
            "client.id": "pi-agents-flow-replay",
            # Deterministic key -> partition mapping (same as the Java
            # client), so every record of a device lands on one partition.
            "partitioner": "murmur2_random",
            "enable.idempotence": True,
            "acks": "all",
            "linger.ms": 50,
            "compression.type": "lz4",
        }
    )
    try:
        metadata = consumer.list_topics(args.source_topic, timeout=30)
        topic = metadata.topics.get(args.source_topic)
        if topic is None or topic.error is not None or not topic.partitions:
            log.error("source topic %s not found: %s", args.source_topic, topic and topic.error)
            return 1
        partitions = sorted(topic.partitions)

        end_offsets = {}
        for p in partitions:
            _low, high = consumer.get_watermark_offsets(TopicPartition(args.source_topic, p), timeout=30)
            end_offsets[p] = high
        found = consumer.offsets_for_times(
            [TopicPartition(args.source_topic, p, window_start_ms) for p in partitions],
            timeout=30,
        )
        start_offsets = {tp.partition: tp.offset for tp in found}
        ranges = plan(start_offsets, end_offsets)
        log.info(
            "window %s .. %s (%.1f h): %d records in %d partitions",
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(window_start_ms / 1000)),
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(run_start_ms / 1000)),
            args.window_hours,
            sum(r.count for r in ranges),
            len(ranges),
        )
        if not ranges:
            print(json.dumps({"window_start_ms": window_start_ms, "run_start_ms": run_start_ms, "copied": 0}))
            return 0

        consumer.assign([TopicPartition(args.source_topic, r.partition, r.start) for r in ranges])
        stats = copy(
            consumer,
            producer,
            ranges,
            source_topic=args.source_topic,
            target_topic=args.target_topic,
            source_label=args.source_label,
            limiter=RateLimiter(args.max_rate),
            max_messages=args.max_messages,
            make_partition=TopicPartition,
        )
    finally:
        consumer.close()

    summary = {"window_start_ms": window_start_ms, "run_start_ms": run_start_ms, **stats.as_dict()}
    print(json.dumps(summary))
    return 1 if stats.delivery_errors else 0


if __name__ == "__main__":
    sys.exit(main())
