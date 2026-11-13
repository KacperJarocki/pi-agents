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
        self.assertIn('syntax = "proto3"', proto)
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

    def test_roadmap_names_the_legacy_path_and_its_removal(self):
        roadmap = (ROOT / "docs" / "STREAMING-ROADMAP.md").read_text()
        self.assertIn("legacy path", roadmap)
        self.assertIn("thesis-final", roadmap)

    def test_configuration_document_keeps_sqlite_as_explicit_legacy_fallback(self):
        config = (ROOT / "docs" / "CONFIGURATION.md").read_text()
        self.assertIn("DATABASE_PATH", config)
        self.assertIn("legacy SQLite fallback", config)
        self.assertIn("CONTROL_DATABASE_URL", config)

    def test_streaming_manifest_is_opt_in(self):
        config_map = (ROOT / "k8s" / "base" / "streaming-config.yaml").read_text()
        self.assertIn('STREAMING_ENABLED: "false"', config_map)

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

    def test_incident_api_exposes_metrics_and_opt_in_alerting(self):
        chart = ROOT / "charts" / "pi-agents"
        values = (chart / "values.yaml").read_text()
        template = (chart / "templates" / "incidents.yaml").read_text()
        api_main = (ROOT / "images" / "incident-api" / "app" / "main.py").read_text()
        # The API serves a Prometheus /metrics endpoint.
        self.assertIn('@app.get("/metrics")', api_main)
        self.assertIn("pi_agents_active_incidents", api_main)
        # Scrape + alert are opt-in and wired to Alertmanager routing labels.
        self.assertIn("monitoring:", values)
        self.assertIn("team: security", values)
        self.assertIn("kind: ServiceMonitor", template)
        self.assertIn("kind: PrometheusRule", template)
        self.assertIn("alert: PortScanActive", template)
        self.assertIn("$a.monitoring.enabled", template)

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
        self.assertIn("k8s/argocd/strimzi-operator.yaml", readme)

    def test_kafka_chart_defines_v2_topics_off_the_gateway(self):
        chart = ROOT / "charts" / "pi-agents"
        values = (chart / "values.yaml").read_text()
        kafka = (chart / "templates" / "kafka.yaml").read_text()
        for topic in (
            "traffic.flows.v2",
            "traffic.features.v2",
            "traffic.detections.v2",
            "traffic.health.v2",
            "traffic.late.v2",
        ):
            self.assertIn(f"name: {topic}", values)
        self.assertIn("kind: KafkaTopic", kafka)
        self.assertIn("excludeNodeLabel: node-role.kubernetes.io/gateway", values)
        self.assertIn("operator: NotIn", kafka)
        self.assertIn("min.insync.replicas: {{ $.Values.kafka.minInsyncReplicas }}", kafka)
        self.assertIn("minInsyncReplicas: 2", values)
        self.assertIn("eventSchemaVersion: v2", values)

    def test_flink_defaults_to_rustfs_checkpoints(self):
        chart = ROOT / "charts" / "pi-agents"
        values = (chart / "values.yaml").read_text()
        rustfs = (chart / "templates" / "rustfs.yaml").read_text()
        profile = (chart / "values-flink-s3.yaml").read_text()
        self.assertIn("backend: s3", values)
        self.assertIn("taskSlots: 2", values)
        self.assertIn("taskManagerReplicas: 2", values)
        self.assertIn("credentialsSecret: rustfs-credentials", values)
        self.assertIn("rustfs:\n  enabled: true", profile)
        self.assertIn("kind: Deployment", rustfs)
        self.assertIn("helm.sh/hook: post-install,post-upgrade", rustfs)
        self.assertIn("secretKeyRef", rustfs)
        self.assertIn("operator: DoesNotExist", rustfs)
        self.assertIn("resources:", rustfs)

    def test_postgres_cluster_backs_up_to_rustfs(self):
        chart = ROOT / "charts" / "pi-agents"
        values = (chart / "values.yaml").read_text()
        postgres = (chart / "templates" / "postgres.yaml").read_text()
        argocd = ROOT / "k8s" / "argocd"
        operator = (argocd / "cnpg-operator.yaml").read_text()
        plugin = (argocd / "barman-cloud.yaml").read_text()
        platform = (argocd / "pi-agents.yaml").read_text()
        self.assertIn("instances: 2", values)
        self.assertIn("kind: Cluster", postgres)
        self.assertIn("kind: ObjectStore", postgres)
        self.assertIn("kind: ScheduledBackup", postgres)
        self.assertIn("barman-cloud.cloudnative-pg.io", postgres)
        self.assertIn("isWALArchiver: true", postgres)
        self.assertIn("chart: cloudnative-pg", operator)
        self.assertIn("chart: plugin-barman-cloud", plugin)
        # Sync waves: operator (0) -> plugin (1) -> platform (2).
        self.assertIn('sync-wave: "0"', operator)
        self.assertIn('sync-wave: "1"', plugin)
        self.assertIn('sync-wave: "2"', platform)

    def test_flink_job_builds_from_the_shared_v2_contract(self):
        job = ROOT / "flink" / "detection-job"
        pom = (job / "pom.xml").read_text()
        dockerfile = (job / "Dockerfile").read_text()
        source = job / "src" / "main" / "java" / "io" / "piagents" / "detection"
        self.assertIn("../../schemas/events/v2", pom)
        self.assertIn("<exclude>baseline.proto</exclude>", pom)
        self.assertIn("<flink.version>1.20.5</flink.version>", pom)
        self.assertIn("flink:1.20.5-scala_2.12-java17", dockerfile)
        self.assertIn('LATE_TOPIC = "traffic.late.v2"', (source / "DetectionJob.java").read_text())
        self.assertIn("enableTimeToLive", (source / "DedupeAndLateness.java").read_text())

    def test_detection_job_is_deployable_from_the_chart(self):
        chart = ROOT / "charts" / "pi-agents"
        flink = (chart / "templates" / "flink.yaml").read_text()
        values = (chart / "values.yaml").read_text()
        docs = (ROOT / "docs" / "STREAMING-DETECTION.md").read_text()
        self.assertIn("local:///opt/flink/usrlib/detection-job.jar", flink)
        self.assertIn("upgradeMode: last-state", flink)
        self.assertIn("detectionJob:", values)
        self.assertIn("openThreshold60s: 20", values)
        self.assertIn("openThreshold900s: 40", values)
        for heading in ("## Rolling views", "## Port-scan rule v1", "## Observability", "## Delivery guarantees"):
            self.assertIn(heading, docs)

    def test_strimzi_operator_is_a_pinned_separate_chart_dependency(self):
        chart = (ROOT / "charts" / "strimzi-operator" / "Chart.yaml").read_text()
        values = (ROOT / "charts" / "strimzi-operator" / "values.yaml").read_text()
        self.assertIn("alias: strimzi", chart)
        self.assertIn("version: 1.2.0", chart)
        self.assertIn("repository: oci://quay.io/strimzi-helm", chart)
        self.assertIn("watchNamespaces", values)

    def test_argocd_orders_operator_before_platform(self):
        argocd = ROOT / "k8s" / "argocd"
        operator = (argocd / "strimzi-operator.yaml").read_text()
        platform = (argocd / "pi-agents.yaml").read_text()
        self.assertIn("path: charts/strimzi-operator", operator)
        self.assertIn("kind: ApplicationSet", platform)
        self.assertIn("path: charts/pi-agents", platform)
        self.assertIn("../../k8s/envs/{{ .env }}/images.yaml", platform)
        self.assertIn('sync-wave: "0"', operator)
        self.assertIn('sync-wave: "2"', platform)
        for app in argocd.glob("*.yaml"):
            self.assertIn("project: pi-agents", app.read_text(), app.name)
        namespaces = (argocd / "namespaces.yaml").read_text()
        self.assertIn('sync-wave: "-1"', namespaces)
        self.assertIn("name: iot-security-dev", (ROOT / "k8s" / "namespaces" / "iot-security-dev.yaml").read_text())

    def test_environments_share_base_values_and_promote_image_tags(self):
        envs = ROOT / "k8s" / "envs"
        base = (envs / "base.yaml").read_text()
        dev = (envs / "dev" / "values.yaml").read_text()
        self.assertIn("streaming:\n  enabled: true", base)
        self.assertNotIn("tag:", base)
        self.assertIn("minInsyncReplicas: 1", dev)
        self.assertIn("backend: longhorn", dev)
        for env in ("dev", "prod"):
            images = (envs / env / "images.yaml").read_text()
            for key in ("detectionJob", "sensor", "projector", "api"):
                self.assertIn(key, images, env)
            self.assertNotIn("tag: latest", images, env)


if __name__ == "__main__":
    unittest.main()
