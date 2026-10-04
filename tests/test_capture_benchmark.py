import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "benchmark_capture.py"
SPEC = importlib.util.spec_from_file_location("benchmark_capture", MODULE_PATH)
benchmark_capture = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(benchmark_capture)


class CaptureBenchmarkTests(unittest.TestCase):
    def test_commands_are_unprivileged_and_reproducible(self):
        output = pathlib.Path("/tmp/capture.pcap")
        self.assertEqual(
            benchmark_capture.build_tcpdump_command("wlan0", output, 100, 128),
            ["tcpdump", "-i", "wlan0", "-n", "-p", "-s", "128", "-w", "/tmp/capture.pcap", "-c", "100"],
        )
        self.assertEqual(
            benchmark_capture.build_tshark_command(output),
            ["tshark", "-r", "/tmp/capture.pcap", "-T", "fields", "-e", "frame.number"],
        )

    def test_dry_run_never_executes_capture_tools(self):
        result = benchmark_capture.run_benchmark(
            interface="wlan0",
            output=pathlib.Path("/tmp/capture.pcap"),
            packet_count=10,
            snaplen=96,
            duration_seconds=1,
            dry_run=True,
        )
        self.assertTrue(result["dry_run"])
        self.assertIsNone(result["capture_returncode"])
        self.assertIsNone(result["parsed_packets"])
        self.assertEqual(result["packet_target"], 10)


if __name__ == "__main__":
    unittest.main()
