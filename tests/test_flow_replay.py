"""Prod -> dev replay of traffic.flows.v2 (images/flow-replay) without Kafka."""

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "images" / "flow-replay" / "app" / "replay.py"

_spec = importlib.util.spec_from_file_location("flow_replay", SCRIPT)
replay = importlib.util.module_from_spec(_spec)
sys.modules["flow_replay"] = replay  # dataclasses look the module up
_spec.loader.exec_module(replay)


class FakeMessage:
    def __init__(self, partition, offset, key=b"gw:aa", value=b"v", ts=1_000, headers=None):
        self._partition = partition
        self._offset = offset
        self._key = key
        self._value = value
        self._ts = ts
        self._headers = headers

    def error(self):
        return None

    def partition(self):
        return self._partition

    def offset(self):
        return self._offset

    def key(self):
        return self._key

    def value(self):
        return self._value

    def timestamp(self):
        return (1, self._ts)

    def headers(self):
        return self._headers


class FakeConsumer:
    def __init__(self, batches):
        self.batches = list(batches)
        self.paused = []

    def consume(self, _n, _timeout):
        return self.batches.pop(0) if self.batches else []

    def pause(self, partitions):
        self.paused.extend(partitions)


class FakeProducer:
    def __init__(self, fail_first_produce=False, undelivered=0):
        self.records = []
        self.fail_first = fail_first_produce
        self.undelivered = undelivered

    def produce(self, topic, value, key, timestamp, headers, on_delivery):
        if self.fail_first:
            self.fail_first = False
            raise BufferError("queue full")
        self.records.append((topic, key, value, timestamp, headers))
        on_delivery(None, None)

    def poll(self, _timeout):
        return 0

    def flush(self, _timeout):
        return self.undelivered


def run_copy(consumer, producer, ranges, **kw):
    return replay.copy(
        consumer,
        producer,
        ranges,
        source_topic="traffic.flows.v2",
        target_topic="traffic.flows.v2",
        source_label="prod",
        limiter=replay.RateLimiter(0),
        make_partition=lambda topic, p: (topic, p),
        **kw,
    )


class PlanTests(unittest.TestCase):
    def test_skips_partitions_without_records_in_window(self):
        ranges = replay.plan({0: 10, 1: -1, 2: 7}, {0: 15, 1: 30, 2: 7})
        self.assertEqual(ranges, [replay.Range(0, 10, 15)])
        self.assertEqual(ranges[0].count, 5)


class RateLimiterTests(unittest.TestCase):
    def test_spaces_records_to_the_rate(self):
        now = [100.0]
        slept = []

        def sleep(s):
            slept.append(s)
            now[0] += s

        limiter = replay.RateLimiter(10, clock=lambda: now[0], sleep=sleep)
        for _ in range(3):
            limiter.acquire()
        self.assertEqual(len(slept), 2)
        self.assertAlmostEqual(sum(slept), 0.2)

    def test_zero_rate_never_sleeps(self):
        limiter = replay.RateLimiter(0, clock=lambda: 0.0, sleep=self.fail)
        limiter.acquire()


class CopyTests(unittest.TestCase):
    def test_copies_range_byte_for_byte_and_tags_source(self):
        consumer = FakeConsumer(
            [
                [FakeMessage(0, 10, key=b"gw:a", value=b"x1", ts=111, headers=[("h", b"1")])],
                [FakeMessage(0, 11, key=b"gw:a", value=b"x2", ts=112), FakeMessage(0, 12)],
            ]
        )
        producer = FakeProducer(fail_first_produce=True)
        stats = run_copy(consumer, producer, [replay.Range(0, 10, 12)])

        self.assertEqual(stats.copied, 2)
        self.assertEqual(stats.delivery_errors, 0)
        self.assertEqual(stats.partitions, {0: 2})
        first = producer.records[0]
        self.assertEqual(first[:4], ("traffic.flows.v2", b"gw:a", b"x1", 111))
        self.assertEqual(first[4], [("h", b"1"), (replay.REPLAY_HEADER, b"prod")])
        # Stops at the planned end; offset 12 (after the snapshot) is not copied.
        self.assertEqual([r[2] for r in producer.records], [b"x1", b"x2"])
        self.assertIn(("traffic.flows.v2", 0), consumer.paused)

    def test_max_messages_caps_the_run(self):
        consumer = FakeConsumer([[FakeMessage(0, o) for o in range(5)]])
        producer = FakeProducer()
        stats = run_copy(consumer, producer, [replay.Range(0, 0, 5)], max_messages=3)
        self.assertEqual(stats.copied, 3)
        self.assertEqual(stats.planned, 3)

    def test_idle_source_with_unfinished_range_fails(self):
        ticks = iter(range(0, 1000, 30))
        with self.assertRaises(RuntimeError):
            run_copy(
                FakeConsumer([]), FakeProducer(), [replay.Range(0, 0, 5)], idle_timeout=60, clock=lambda: next(ticks)
            )

    def test_undelivered_records_count_as_errors(self):
        consumer = FakeConsumer([[FakeMessage(0, 0)]])
        stats = run_copy(consumer, FakeProducer(undelivered=1), [replay.Range(0, 0, 1)])
        self.assertEqual(stats.delivery_errors, 1)


class ArgsTests(unittest.TestCase):
    def test_refuses_to_copy_a_topic_onto_itself(self):
        with self.assertRaises(SystemExit):
            replay.parse_args(["--source-bootstrap", "k:9092", "--target-bootstrap", "k:9092"])


class DeploymentSourceTests(unittest.TestCase):
    def test_enabled_only_in_dev_and_reads_prod(self):
        dev = (ROOT / "k8s" / "envs" / "dev" / "values.yaml").read_text()
        prod = (ROOT / "k8s" / "envs" / "prod" / "values.yaml").read_text()
        values = (ROOT / "charts" / "pi-agents" / "values.yaml").read_text()
        self.assertIn("flowReplay:\n  enabled: true", dev)
        self.assertIn("pi-agents-kafka-bootstrap.iot-security.svc:9092", dev)
        self.assertNotIn("flowReplay", prod)
        self.assertIn("flowReplay:\n  enabled: false", values)

    def test_cronjob_runs_at_five_with_limits(self):
        tpl = (ROOT / "charts" / "pi-agents" / "templates" / "flow-replay.yaml").read_text()
        values = (ROOT / "charts" / "pi-agents" / "values.yaml").read_text()
        self.assertIn("kind: CronJob", tpl)
        self.assertIn("concurrencyPolicy: Forbid", tpl)
        self.assertIn("toYaml $r.resources", tpl)
        self.assertIn('schedule: "0 5 * * *"', values)
        self.assertIn("timeZone: Europe/Warsaw", values)

    def test_image_is_built_and_bumped_in_dev(self):
        wf = (ROOT / ".github" / "workflows" / "docker-build.yml").read_text()
        self.assertIn("image_name: flow-replay", wf)
        self.assertIn(".flowReplay.image.tag = strenv(TAG)", wf)


if __name__ == "__main__":
    unittest.main()
