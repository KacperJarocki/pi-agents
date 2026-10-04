import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class StreamingArchitectureSourceTests(unittest.TestCase):
    def test_backlog_defines_ready_done_and_milestones(self):
        roadmap = (ROOT / "docs" / "STREAMING-ROADMAP.md").read_text()
        for marker in (
            "## Definition of Ready",
            "## Definition of Done",
            "## Milestones",
            "ARCH-001",
            "EVT-001",
            "PLAT-004",
            "FLINK-005",
            "UI-001",
            "MIG-004",
        ):
            self.assertIn(marker, roadmap)

    def test_event_contract_has_versioned_envelope_and_required_domains(self):
        proto = (ROOT / "schemas" / "events" / "v1" / "events.proto").read_text()
        self.assertIn("syntax = \"proto3\"", proto)
        for field in (
            "event_id",
            "schema_version",
            "event_time_unix_millis",
            "ingest_time_unix_millis",
            "gateway_id",
            "device_key",
            "trace_id",
        ):
            self.assertRegex(proto, rf"\b{field}\s*=")
        for message in ("FlowEvent", "FeatureVector", "Detection", "CaptureObject"):
            self.assertRegex(proto, rf"message\s+{message}\s*\{{")

    def test_event_contract_uses_monotonic_field_numbers(self):
        proto = (ROOT / "schemas" / "events" / "v1" / "events.proto").read_text()
        for message in ("EventEnvelope", "FlowEvent", "FeatureVector", "Detection", "CaptureObject"):
            body = re.search(rf"message\s+{message}\s*\{{(.*?)\n\}}", proto, re.S)
            self.assertIsNotNone(body, message)
            numbers = [int(value) for value in re.findall(r"=\s*(\d+)\s*;", body.group(1))]
            self.assertEqual(numbers, sorted(numbers), message)
            self.assertEqual(len(numbers), len(set(numbers)), message)

    def test_legacy_runtime_is_not_claimed_to_be_streaming(self):
        inference = (ROOT / "images" / "ml-pipeline" / "app" / "inference.py").read_text()
        self.assertIn("INFERENCE_INTERVAL", inference)
        self.assertIn("run_inference_loop", inference)
        roadmap = (ROOT / "docs" / "STREAMING-ROADMAP.md").read_text()
        self.assertIn("legacy path", roadmap)

    def test_gateway_api_exposes_external_service_configuration(self):
        config = (ROOT / "images" / "gateway-api" / "app" / "core" / "config.py").read_text()
        for env_name, field_name in (
            ("CONTROL_DATABASE_URL", "control_database_url"),
            ("CLICKHOUSE_URL", "clickhouse_url"),
            ("OBJECT_STORAGE_ENDPOINT", "object_storage_endpoint"),
            ("KAFKA_BOOTSTRAP_SERVERS", "kafka_bootstrap_servers"),
            ("EVENT_SCHEMA_VERSION", "event_schema_version"),
        ):
            self.assertIn(env_name, config)
            self.assertIn(field_name, config)

    def test_configuration_document_keeps_sqlite_as_explicit_legacy_fallback(self):
        config = (ROOT / "docs" / "CONFIGURATION.md").read_text()
        self.assertIn("DATABASE_PATH", config)
        self.assertIn("legacy SQLite fallback", config)
        self.assertIn("CONTROL_DATABASE_URL", config)

    def test_streaming_manifest_is_opt_in_and_external_endpoints_are_secret_backed(self):
        config_map = (ROOT / "k8s" / "base" / "streaming-config.yaml").read_text()
        deployment = (ROOT / "k8s" / "gateway" / "gateway-api-deployment.yaml").read_text()
        self.assertIn('STREAMING_ENABLED: "false"', config_map)
        self.assertIn("iot-security-external-services", deployment)
        self.assertIn("control-database-url", deployment)
        self.assertIn("clickhouse-url", deployment)
        self.assertIn("object-storage-endpoint", deployment)
        self.assertIn("optional: true", deployment)

    def test_streaming_config_is_included_by_base_kustomization(self):
        kustomization = (ROOT / "k8s" / "base" / "kustomization.yaml").read_text()
        self.assertIn("streaming-config.yaml", kustomization)

    def test_helm_chart_exposes_internal_external_storage_switches(self):
        chart = ROOT / "charts" / "pi-agents"
        self.assertTrue((chart / "Chart.yaml").exists())
        values = (chart / "values.yaml").read_text()
        external_values = (chart / "values-external.yaml").read_text()
        config_template = (chart / "templates" / "streaming-configmap.yaml").read_text()
        secret_template = (chart / "templates" / "secret-reference.yaml").read_text()
        self.assertIn("streaming:", values)
        self.assertIn("enabled: false", values)
        self.assertIn("externalServices:", values)
        self.assertIn("existingSecret", values)
        self.assertIn("streaming:\n  enabled: true", external_values)
        self.assertIn("iot-security-external-services", external_values)
        self.assertIn("KAFKA_BOOTSTRAP_SERVERS", config_template)
        self.assertIn("EXTERNAL_SERVICES_SECRET_NAME", secret_template)

    def test_external_service_credentials_are_not_chart_values(self):
        values = (ROOT / "charts" / "pi-agents" / "values.yaml").read_text()
        for forbidden in ("password:", "accessKey:", "secretKey:", "postgres://"):
            self.assertNotIn(forbidden, values)

    def test_helm_chart_has_opt_in_strimzi_kafka_cluster(self):
        chart = ROOT / "charts" / "pi-agents"
        values = (chart / "values.yaml").read_text()
        kafka = (chart / "templates" / "kafka.yaml").read_text()
        profile = (chart / "values-kafka-cluster.yaml").read_text()
        readme = (chart / "README.md").read_text()
        self.assertIn("enabled: false", values)
        self.assertIn("kind: Kafka", kafka)
        self.assertIn("kind: KafkaNodePool", kafka)
        self.assertIn("apiVersion: kafka.strimzi.io/v1", kafka)
        self.assertIn('version: "4.3.1"', values)
        self.assertIn('metadataVersion: "4.3-IV0"', values)
        self.assertIn("type: persistent-claim", values)
        self.assertIn("enabled: true", profile)
        self.assertIn("streaming-kustomization.yaml", readme)

    def test_strimzi_operator_is_a_pinned_separate_chart_dependency(self):
        chart = (ROOT / "charts" / "strimzi-operator" / "Chart.yaml").read_text()
        values = (ROOT / "charts" / "strimzi-operator" / "values.yaml").read_text()
        self.assertIn("alias: strimzi", chart)
        self.assertIn("version: 1.2.0", chart)
        self.assertIn("repository: oci://quay.io/strimzi-helm", chart)
        self.assertIn("watchNamespaces", values)

    def test_flux_orders_operator_before_platform(self):
        operator = (ROOT / "k8s" / "flux" / "streaming-operator-helmrelease.yaml").read_text()
        platform = (ROOT / "k8s" / "flux" / "streaming-platform-helmrelease.yaml").read_text()
        source = (ROOT / "k8s" / "flux" / "streaming-source.yaml").read_text()
        kustomization = (ROOT / "k8s" / "flux" / "streaming-kustomization.yaml").read_text()
        self.assertIn("chart: ./charts/strimzi-operator", operator)
        self.assertIn("chart: ./charts/pi-agents", platform)
        self.assertIn("name: strimzi-operator", platform)
        self.assertIn("name: pi-agents", source)
        self.assertIn("path: ./k8s/flux", kustomization)
        self.assertIn("name: pi-agents", kustomization)


if __name__ == "__main__":
    unittest.main()
