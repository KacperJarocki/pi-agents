import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RustSensorSourceTests(unittest.TestCase):
    def test_sensor_core_is_dependency_free_and_bounded(self):
        cargo = (ROOT / "sensors" / "rust-sensor" / "Cargo.toml").read_text()
        source = (ROOT / "sensors" / "rust-sensor" / "src" / "lib.rs").read_text()
        self.assertIn('edition = "2021"', cargo)
        self.assertNotIn("[dependencies]", cargo)
        self.assertIn("pub struct FlowTable", source)
        self.assertIn("pub fn expire_before", source)
        self.assertIn("UpsertResult::Evicted", source)

    def test_sensor_core_does_not_hide_overflow(self):
        readme = (ROOT / "sensors" / "rust-sensor" / "README.md").read_text()
        self.assertIn("UpsertResult::Evicted", readme)


if __name__ == "__main__":
    unittest.main()
