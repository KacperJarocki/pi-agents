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

    def test_arm64_sensor_workflow_checks_format_tests_and_target(self):
        workflow = (ROOT / ".github" / "workflows" / "rust-sensor.yml").read_text()
        self.assertIn("aarch64-unknown-linux-gnu", workflow)
        self.assertIn("cargo fmt", workflow)
        self.assertIn("cargo test", workflow)
        self.assertIn("cargo check", workflow)


if __name__ == "__main__":
    unittest.main()
