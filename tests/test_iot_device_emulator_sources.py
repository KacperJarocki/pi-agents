import unittest
from pathlib import Path


class TestIotDeviceEmulatorSources(unittest.TestCase):
    def test_iot_device_emulator_has_baseline_profiles(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "iot-device-emulator.py").read_text()

        self.assertIn('"sensor"', src)
        self.assertIn('"plug"', src)
        self.assertIn('"camera-idle"', src)
        self.assertIn("socket.getaddrinfo", src)
        self.assertIn("socket.create_connection", src)
        self.assertIn("udp_heartbeat", src)
        self.assertIn("events.jsonl", src)
        self.assertIn("summary.json", src)

    def test_iot_device_emulator_wrapper_exists(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "iot-device-emulator.sh").read_text()

        self.assertIn("iot-device-emulator.py", src)
        self.assertIn("$@", src)


if __name__ == "__main__":
    unittest.main()
