package io.piagents.detection;

import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.api.common.typeinfo.Types;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;

/**
 * Limits feature output to one view set per device per interval (default
 * 1 s): the first change goes out at once, later ones within the interval are
 * held and the latest is sent when it ends. Each emitted set becomes one
 * FeatureUpdate per horizon. Rule evaluation is not affected (it reads the
 * unthrottled views).
 */
public class FeatureThrottle
    extends KeyedProcessFunction<String, DeviceWindows.ViewSet, DeviceWindows.View> {
  private final long intervalMs;

  private transient ValueState<Long> lastEmitMs;
  private transient ValueState<DeviceWindows.ViewSet> pending;

  public FeatureThrottle(long intervalMs) {
    this.intervalMs = intervalMs;
  }

  @Override
  public void open(Configuration parameters) {
    lastEmitMs = getRuntimeContext().getState(new ValueStateDescriptor<>("last-emit", Types.LONG));
    pending =
        getRuntimeContext()
            .getState(
                new ValueStateDescriptor<>(
                    "pending", TypeInformation.of(DeviceWindows.ViewSet.class)));
  }

  @Override
  public void processElement(
      DeviceWindows.ViewSet set, Context ctx, Collector<DeviceWindows.View> out) throws Exception {
    long now = ctx.timerService().currentProcessingTime();
    Long last = lastEmitMs.value();
    if (last == null || now - last >= intervalMs) {
      emit(set, out);
      lastEmitMs.update(now);
      return;
    }
    if (pending.value() == null) {
      ctx.timerService().registerProcessingTimeTimer(last + intervalMs);
    }
    pending.update(set);
  }

  @Override
  public void onTimer(long timestamp, OnTimerContext ctx, Collector<DeviceWindows.View> out)
      throws Exception {
    DeviceWindows.ViewSet set = pending.value();
    if (set != null) {
      emit(set, out);
      lastEmitMs.update(timestamp);
      pending.clear();
    } else {
      lastEmitMs.clear();
    }
  }

  private static void emit(DeviceWindows.ViewSet set, Collector<DeviceWindows.View> out) {
    for (DeviceWindows.View v : set.views) {
      out.collect(v);
    }
  }
}
