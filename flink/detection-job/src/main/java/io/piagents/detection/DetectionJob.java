package io.piagents.detection;

import java.time.Duration;
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
import org.apache.flink.streaming.api.functions.sink.v2.DiscardingSink;
import org.apache.flink.util.Collector;

/**
 * Reads {@code traffic.flows.v2}, drops duplicates, routes malformed and
 * too-late events to {@code traffic.late.v2}, and (in later tasks) maintains
 * per-device sliding windows and the port-scan rule.
 */
public final class DetectionJob {
  public static final String FLOWS_TOPIC = "traffic.flows.v2";
  public static final String LATE_TOPIC = "traffic.late.v2";
  public static final Duration DEFAULT_LATENESS = Duration.ofSeconds(30);
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

  public static void main(String[] args) throws Exception {
    ParameterTool params = ParameterTool.fromArgs(args);
    String bootstrap = params.get("bootstrap", "pi-agents-kafka-bootstrap:9092");
    Duration lateness = Duration.ofMillis(params.getLong("lateness-ms", DEFAULT_LATENESS.toMillis()));

    StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

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

    // Replaced by the sliding-window state and port-scan rule (tasks 5.2-5.3).
    ingest.accepted().sinkTo(new DiscardingSink<>()).name("detection-placeholder");

    env.execute("pi-agents detection");
  }
}
