package io.piagents.detection;

import java.time.Duration;
import java.util.Properties;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.datastream.SingleOutputStreamOperator;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.ProcessFunction;
import org.apache.flink.util.Collector;

/**
 * Reads {@code traffic.flows.v2}, drops duplicates, routes malformed and
 * too-late events to {@code traffic.late.v2}, and (in later tasks) maintains
 * per-device sliding windows and the port-scan rule.
 */
public final class DetectionJob {
  public static final String FLOWS_TOPIC = "traffic.flows.v2";
  public static final String LATE_TOPIC = "traffic.late.v2";
  public static final String DETECTIONS_TOPIC = "traffic.detections.v2";
  public static final String FEATURES_TOPIC = "traffic.features.v2";
  public static final long FEATURE_INTERVAL_MS = 1_000;
  public static final Duration DEFAULT_LATENESS = Duration.ofSeconds(30);
  public static final Duration SHORT_HORIZON = Duration.ofSeconds(60);
  public static final Duration LONGEST_HORIZON = Duration.ofSeconds(900);

  private DetectionJob() {}

  /** The accepted flows and everything routed to the late topic. */
  public record Ingest(DataStream<FlowRecord> accepted, DataStream<LateEvent> late) {}

  /** Validation, dedupe and lateness, shared by the job and its tests. */
  public static Ingest ingest(DataStream<FlowRecord> flows, Duration lateness) {
    SingleOutputStreamOperator<FlowRecord> valid =
        flows
            .process(
                new ProcessFunction<FlowRecord, FlowRecord>() {
                  @Override
                  public void processElement(
                      FlowRecord record, Context ctx, Collector<FlowRecord> out) {
                    if (record.malformedReason != null) {
                      ctx.output(
                          DedupeAndLateness.LATE,
                          LateEvent.of(LateEvent.MALFORMED, record.malformedReason, record, 0));
                    } else {
                      out.collect(record);
                    }
                  }
                })
            .name("validate")
            .uid("validate");
    SingleOutputStreamOperator<FlowRecord> accepted =
        valid
            .keyBy(r -> r.deviceKey)
            .process(new DedupeAndLateness(lateness, LONGEST_HORIZON))
            .name("dedupe-and-lateness")
            .uid("dedupe-and-lateness");
    DataStream<LateEvent> late =
        valid
            .getSideOutput(DedupeAndLateness.LATE)
            .union(accepted.getSideOutput(DedupeAndLateness.LATE));
    return new Ingest(accepted, late);
  }

  /** Per-device rolling views over both horizons, emitted on change. */
  public static DataStream<DeviceWindows.ViewSet> views(DataStream<FlowRecord> accepted) {
    return accepted
        .keyBy(r -> r.deviceKey)
        .process(new RollingViews(SHORT_HORIZON.toMillis(), LONGEST_HORIZON.toMillis()))
        .name("rolling-views")
        .uid("rolling-views");
  }

  /** Port-scan transitions from the per-device views. */
  public static DataStream<Transition> detect(
      DataStream<DeviceWindows.ViewSet> views, PortScanRule.Config config) {
    return views
        .keyBy(v -> v.deviceKey)
        .process(new PortScanDetector(config))
        .name("port-scan-rule")
        .uid("port-scan-rule");
  }

  /** At most one feature view per device and horizon per interval. */
  public static DataStream<DeviceWindows.View> features(DataStream<DeviceWindows.ViewSet> views) {
    return views
        .keyBy(v -> v.deviceKey)
        .process(new FeatureThrottle(FEATURE_INTERVAL_MS))
        .name("feature-throttle")
        .uid("feature-throttle");
  }

  static PortScanRule.Config ruleConfig(ParameterTool params) {
    PortScanRule.Config c = new PortScanRule.Config();
    c.openThresholds[0] = params.getDouble("open-threshold-60s", c.openThresholds[0]);
    c.openThresholds[1] = params.getDouble("open-threshold-900s", c.openThresholds[1]);
    c.clearFraction = params.getDouble("clear-fraction", c.clearFraction);
    c.holdMs = params.getLong("hold-ms", c.holdMs);
    c.cooldownMs = params.getLong("cooldown-ms", c.cooldownMs);
    return c;
  }

  public static void main(String[] args) throws Exception {
    ParameterTool params = ParameterTool.fromArgs(args);
    String bootstrap = params.get("bootstrap", "pi-agents-kafka-bootstrap:9092");
    Duration lateness = Duration.ofMillis(params.getLong("lateness-ms", DEFAULT_LATENESS.toMillis()));

    StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
    // Every record and state type has an explicit serializer; fail fast if
    // something would silently fall back to Kryo.
    env.getConfig().disableGenericTypes();

    KafkaSource<FlowRecord> source =
        KafkaSource.<FlowRecord>builder()
            .setBootstrapServers(bootstrap)
            .setTopics(FLOWS_TOPIC)
            .setGroupId(params.get("group-id", "pi-agents-detection"))
            .setStartingOffsets(OffsetsInitializer.earliest())
            .setValueOnlyDeserializer(new FlowEventDeserializer())
            .build();
    DataStream<FlowRecord> flows =
        env.fromSource(source, WatermarkStrategy.noWatermarks(), "traffic.flows.v2")
            .uid("flows-source");

    Ingest ingest = ingest(flows, lateness);

    KafkaSink<LateEvent> lateSink =
        KafkaSink.<LateEvent>builder()
            .setBootstrapServers(bootstrap)
            .setRecordSerializer(new LateEventSerializer(LATE_TOPIC))
            .setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)
            .build();
    ingest.late().sinkTo(lateSink).name("traffic.late.v2").uid("late-sink");

    DataStream<DeviceWindows.ViewSet> views = views(ingest.accepted());
    DataStream<Transition> transitions = detect(views, ruleConfig(params));

    // Observability only: at-least-once, throttled, outside the rule path.
    KafkaSink<DeviceWindows.View> featuresSink =
        KafkaSink.<DeviceWindows.View>builder()
            .setBootstrapServers(bootstrap)
            .setRecordSerializer(new FeatureUpdateSerializer(FEATURES_TOPIC))
            .setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)
            .build();
    features(views).sinkTo(featuresSink).name(FEATURES_TOPIC).uid("features-sink");

    // Exactly-once: transitions are committed with Flink checkpoints. The
    // incident projector reads read_uncommitted and dedupes by transition ID,
    // so it does not wait for the checkpoint interval.
    Properties producer = new Properties();
    // Must not exceed the broker's transaction.max.timeout.ms (15 min default).
    producer.setProperty("transaction.timeout.ms", "900000");
    KafkaSink<Transition> detectionsSink =
        KafkaSink.<Transition>builder()
            .setBootstrapServers(bootstrap)
            .setRecordSerializer(new TransitionSerializer(DETECTIONS_TOPIC))
            .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
            .setTransactionalIdPrefix(params.get("transactional-id-prefix", "pi-agents-detection"))
            .setKafkaProducerConfig(producer)
            .build();
    transitions.sinkTo(detectionsSink).name(DETECTIONS_TOPIC).uid("detections-sink");

    env.execute("pi-agents detection");
  }
}
