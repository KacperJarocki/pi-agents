package io.piagents.detection;

import java.time.Duration;
import org.apache.flink.api.common.state.MapState;
import org.apache.flink.api.common.state.MapStateDescriptor;
import org.apache.flink.api.common.state.StateTtlConfig;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.common.typeinfo.Types;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.metrics.Counter;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;
import org.apache.flink.util.OutputTag;

/**
 * Per device (keyed by device key): drops events whose ID was already seen and
 * sends events older than the device clock minus the allowed lateness to the
 * late side output. The device clock is the maximum event time seen.
 *
 * <p>Event IDs are remembered for the longest horizon plus the lateness
 * (state TTL), so a replay within that time is not counted twice.
 */
public class DedupeAndLateness extends KeyedProcessFunction<String, FlowRecord, FlowRecord> {

  public static final OutputTag<LateEvent> LATE = new OutputTag<>("late") {};

  private final long latenessMs;
  private final Duration dedupeRetention;

  private transient ValueState<Long> deviceClock;
  private transient MapState<String, Long> seen;
  private transient Counter duplicates;
  private transient Counter tooLate;

  public DedupeAndLateness(Duration lateness, Duration longestHorizon) {
    this.latenessMs = lateness.toMillis();
    this.dedupeRetention = longestHorizon.plus(lateness);
  }

  @Override
  public void open(Configuration parameters) {
    deviceClock =
        getRuntimeContext().getState(new ValueStateDescriptor<>("device-clock", Types.LONG));
    MapStateDescriptor<String, Long> seenDescriptor =
        new MapStateDescriptor<>("seen-event-ids", Types.STRING, Types.LONG);
    seenDescriptor.enableTimeToLive(
        StateTtlConfig.newBuilder(dedupeRetention)
            .setUpdateType(StateTtlConfig.UpdateType.OnCreateAndWrite)
            .setStateVisibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
            .build());
    seen = getRuntimeContext().getMapState(seenDescriptor);
    duplicates = getRuntimeContext().getMetricGroup().counter("duplicateEvents");
    tooLate = getRuntimeContext().getMetricGroup().counter("lateEvents");
  }

  @Override
  public void processElement(FlowRecord record, Context ctx, Collector<FlowRecord> out)
      throws Exception {
    if (seen.contains(record.eventId)) {
      duplicates.inc();
      return;
    }
    seen.put(record.eventId, record.eventTimeMs);

    Long clock = deviceClock.value();
    if (clock != null && record.eventTimeMs < clock - latenessMs) {
      tooLate.inc();
      ctx.output(
          LATE,
          LateEvent.of(
              LateEvent.TOO_LATE,
              "event time is " + (clock - record.eventTimeMs) + " ms behind the device clock",
              record,
              clock));
      return;
    }
    if (clock == null || record.eventTimeMs > clock) {
      deviceClock.update(record.eventTimeMs);
    }
    out.collect(record);
  }
}
