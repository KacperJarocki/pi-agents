package io.piagents.detection;

import java.util.ArrayList;
import java.util.List;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.api.common.typeinfo.Types;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.metrics.Histogram;
import org.apache.flink.runtime.metrics.DescriptiveStatisticsHistogram;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;

/**
 * Keeps each device's rolling views and emits a {@link DeviceWindows.ViewSet}
 * (every horizon) whenever any horizon's aggregates change, so downstream
 * rules evaluate once per change.
 *
 * <p>The device clock is the maximum event time seen. When the device is
 * silent, a processing-time timer (1 s after the last change, then every
 * 5 s while state remains) advances the clock by the wall time elapsed since
 * the last event, so evidence expires without waiting for new packets or a
 * global watermark. State is cleared once every horizon is empty.
 */
public class RollingViews extends KeyedProcessFunction<String, FlowRecord, DeviceWindows.ViewSet> {

  static final long FIRST_TICK_MS = 1_000;
  static final long TICK_MS = 5_000;

  private final long[] horizonsMs;

  private transient ValueState<DeviceWindows> windows;
  /** Processing time of the last event and the device clock at that time. */
  private transient ValueState<Long> lastEventWallMs;
  private transient ValueState<Long> pendingTimer;
  private transient ValueState<List<DeviceWindows.View>> lastViews;
  private transient Histogram stateSize;

  public RollingViews(long... horizonsMs) {
    this.horizonsMs = horizonsMs;
  }

  @Override
  public void open(Configuration parameters) {
    windows =
        getRuntimeContext()
            .getState(new ValueStateDescriptor<>("windows", DeviceWindowsSerializer.INSTANCE));
    lastEventWallMs =
        getRuntimeContext().getState(new ValueStateDescriptor<>("last-event-wall", Types.LONG));
    pendingTimer =
        getRuntimeContext().getState(new ValueStateDescriptor<>("pending-timer", Types.LONG));
    lastViews =
        getRuntimeContext()
            .getState(
                new ValueStateDescriptor<>(
                    "last-views", Types.LIST(TypeInformation.of(DeviceWindows.View.class))));
    stateSize =
        getRuntimeContext()
            .getMetricGroup()
            .histogram("contributionsPerDevice", new DescriptiveStatisticsHistogram(1000));
  }

  @Override
  public void processElement(FlowRecord record, Context ctx, Collector<DeviceWindows.ViewSet> out)
      throws Exception {
    DeviceWindows w = windows.value();
    if (w == null) {
      w = new DeviceWindows(horizonsMs);
    }
    long previousClock = w.clockMs();
    w.add(record);
    if (w.clockMs() > previousClock) {
      lastEventWallMs.update(ctx.timerService().currentProcessingTime());
    }
    windows.update(w);
    stateSize.update(w.size());
    emitChanged(w, ctx.getCurrentKey(), out);
    schedule(ctx, ctx.timerService().currentProcessingTime() + FIRST_TICK_MS);
  }

  @Override
  public void onTimer(long timestamp, OnTimerContext ctx, Collector<DeviceWindows.ViewSet> out)
      throws Exception {
    pendingTimer.clear();
    DeviceWindows w = windows.value();
    if (w == null) {
      return;
    }
    Long lastWall = lastEventWallMs.value();
    long elapsed = lastWall == null ? 0 : Math.max(0, timestamp - lastWall);
    // The clock at the last event plus the silence since then.
    long silentClock = latestEventTime(w) + elapsed;
    if (w.advanceTo(silentClock)) {
      windows.update(w);
      emitChanged(w, ctx.getCurrentKey(), out);
    }
    if (w.isEmpty()) {
      windows.clear();
      lastEventWallMs.clear();
      lastViews.clear();
    } else {
      schedule(ctx, timestamp + TICK_MS);
    }
  }

  /** The newest event time among the horizons' contributions. */
  private static long latestEventTime(DeviceWindows w) {
    long latest = Long.MIN_VALUE;
    for (java.util.Iterator<DeviceWindows.Horizon> it = w.horizons(); it.hasNext(); ) {
      DeviceWindows.Horizon h = it.next();
      if (!h.contributions.isEmpty()) {
        latest = Math.max(latest, h.contributions.peekLast().eventTimeMs);
      }
    }
    return latest == Long.MIN_VALUE ? w.clockMs() : latest;
  }

  private void emitChanged(DeviceWindows w, String key, Collector<DeviceWindows.ViewSet> out)
      throws Exception {
    List<DeviceWindows.View> previous = lastViews.value();
    List<DeviceWindows.View> current = w.views(key);
    boolean changed = previous == null || previous.size() != current.size();
    for (int i = 0; !changed && i < current.size(); i++) {
      changed = !current.get(i).sameCounts(previous.get(i));
    }
    if (changed) {
      DeviceWindows.ViewSet set = new DeviceWindows.ViewSet();
      set.deviceKey = key;
      set.windowEndMs = w.clockMs();
      set.views = current.toArray(new DeviceWindows.View[0]);
      out.collect(set);
    }
    lastViews.update(new ArrayList<>(current));
  }

  private void schedule(Context ctx, long at) throws Exception {
    Long pending = pendingTimer.value();
    if (pending != null) {
      ctx.timerService().deleteProcessingTimeTimer(pending);
    }
    ctx.timerService().registerProcessingTimeTimer(at);
    pendingTimer.update(at);
  }
}
