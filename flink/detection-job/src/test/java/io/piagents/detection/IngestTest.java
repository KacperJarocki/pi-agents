package io.piagents.detection;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import org.apache.flink.runtime.testutils.MiniClusterResourceConfiguration;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.test.junit5.MiniClusterExtension;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;

/** Runs the ingest stage on a Flink MiniCluster. */
class IngestTest {
  @RegisterExtension
  static final MiniClusterExtension FLINK =
      new MiniClusterExtension(
          new MiniClusterResourceConfiguration.Builder()
              .setNumberTaskManagers(1)
              .setNumberSlotsPerTaskManager(2)
              .build());

  private record Result(List<String> accepted, List<String> late) {}

  private static Result run(List<FlowRecord> input) throws Exception {
    StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
    env.setParallelism(1);
    env.getConfig().disableGenericTypes();
    DataStream<FlowRecord> flows = env.fromData(FlowRecord.class, input.toArray(new FlowRecord[0]));
    DetectionJob.Ingest ingest = DetectionJob.ingest(flows, DetectionJob.DEFAULT_LATENESS);
    // Exercise the full keyed pipeline (views state) under disabled generic types.
    DetectionJob.views(ingest.accepted()).collectAsync();
    Iterator<FlowRecord> accepted = ingest.accepted().collectAsync();
    Iterator<LateEvent> late = ingest.late().collectAsync();
    env.execute("ingest-test");

    List<String> acceptedIds = new ArrayList<>();
    accepted.forEachRemaining(r -> acceptedIds.add(r.eventId));
    List<String> lateIds = new ArrayList<>();
    late.forEachRemaining(e -> lateIds.add(e.reason + ":" + e.eventId));
    return new Result(acceptedIds, lateIds);
  }

  /** The ordering fixture from docs/STREAMING-ROADMAP.md. */
  @Test
  void roadmapOrderingFixture() throws Exception {
    Result result =
        run(
            List.of(
                Events.record(41, "12:00:01"),
                Events.record(42, "12:00:03"),
                Events.record(41, "12:00:01"), // duplicate
                Events.record(40, "12:00:02"), // out of order, within lateness
                Events.record(12, "11:40:00"))); // older than horizon + lateness

    assertEquals(List.of("gw/41", "gw/42", "gw/40"), result.accepted());
    assertEquals(List.of("too_late:gw/12"), result.late());
  }

  @Test
  void malformedEventsGoToTheLateTopicWithTheirReason() throws Exception {
    Result result =
        run(
            List.of(
                Events.record(1, "12:00:00"),
                new FlowEventDeserializer().deserialize(new byte[] {(byte) 0xff, 0x00}),
                Events.record(2, "12:00:01")));

    assertEquals(List.of("gw/1", "gw/2"), result.accepted());
    assertEquals(1, result.late().size());
    assertEquals("malformed:", result.late().get(0));
  }

  @Test
  void eventExactlyAtTheLatenessBoundaryIsAccepted() throws Exception {
    Result result =
        run(
            List.of(
                Events.record(1, "12:00:30"),
                Events.record(2, "12:00:00"), // exactly 30 s behind
                Events.record(3, "11:59:59"))); // 31 s behind

    assertEquals(List.of("gw/1", "gw/2"), result.accepted());
    assertEquals(List.of("too_late:gw/3"), result.late());
  }
}
