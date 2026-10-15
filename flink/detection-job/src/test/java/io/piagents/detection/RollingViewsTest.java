package io.piagents.detection;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import org.apache.flink.api.common.typeinfo.Types;
import org.apache.flink.runtime.checkpoint.OperatorSubtaskState;
import org.apache.flink.streaming.api.operators.KeyedProcessOperator;
import org.apache.flink.streaming.runtime.streamrecord.StreamRecord;
import org.apache.flink.streaming.util.KeyedOneInputStreamOperatorTestHarness;
import org.junit.jupiter.api.Test;

/** RollingViews with processing-time timers, via Flink's operator test harness. */
class RollingViewsTest {

  private static KeyedOneInputStreamOperatorTestHarness<String, FlowRecord, DeviceWindows.View>
      harness() throws Exception {
    KeyedOneInputStreamOperatorTestHarness<String, FlowRecord, DeviceWindows.View> h =
        new KeyedOneInputStreamOperatorTestHarness<>(
            new KeyedProcessOperator<>(new RollingViews(60_000, 900_000)),
            r -> r.deviceKey,
            Types.STRING);
    h.open();
    return h;
  }

  private static List<DeviceWindows.View> drain(
      KeyedOneInputStreamOperatorTestHarness<String, FlowRecord, DeviceWindows.View> h) {
    List<DeviceWindows.View> views = new ArrayList<>();
    for (Object o : h.getOutput()) {
      if (o instanceof StreamRecord<?> r) {
        views.add((DeviceWindows.View) r.getValue());
      }
    }
    h.getOutput().clear();
    return views;
  }

  private static FlowRecord probe(long seq, String time, int port) {
    FlowRecord r = Events.record(seq, time);
    r.remotePort = port;
    return r;
  }

  @Test
  void emitsBothHorizonsOnChangeAndExpiresDuringSilence() throws Exception {
    var h = harness();
    h.setProcessingTime(0);
    h.processElement(probe(1, "12:00:00", 22), 0);
    h.processElement(probe(2, "12:00:01", 23), 0);
    List<DeviceWindows.View> views = drain(h);
    assertEquals(4, views.size());
    assertEquals(2, views.get(2).failedTargets);

    // 61 s of silence: the 60 s view empties, the 900 s view keeps both.
    for (long t = 1_000; t <= 61_000; t += 1_000) {
      h.setProcessingTime(t);
    }
    views = drain(h);
    DeviceWindows.View last60 = views.stream().filter(v -> v.horizonSeconds == 60).reduce((a, b) -> b).orElseThrow();
    assertEquals(0, last60.failedTargets);
    assertTrue(views.stream().noneMatch(v -> v.horizonSeconds == 900), "900 s view unchanged");

    // After 15 minutes everything has expired and the state is gone.
    for (long t = 61_000; t <= 920_000; t += 5_000) {
      h.setProcessingTime(t);
    }
    views = drain(h);
    assertEquals(0, views.get(views.size() - 1).failedTargets);
    assertEquals(0, h.numKeyedStateEntries());
    assertEquals(0, h.numProcessingTimeTimers());
    h.close();
  }

  @Test
  void stateSurvivesSnapshotAndRestore() throws Exception {
    var h = harness();
    h.setProcessingTime(0);
    h.processElement(probe(1, "12:00:00", 22), 0);
    h.processElement(probe(2, "12:00:00", 23), 0);
    OperatorSubtaskState snapshot = h.snapshot(1, 0);
    h.close();

    var restored =
        new KeyedOneInputStreamOperatorTestHarness<String, FlowRecord, DeviceWindows.View>(
            new KeyedProcessOperator<>(new RollingViews(60_000, 900_000)),
            r -> r.deviceKey,
            Types.STRING);
    restored.initializeState(snapshot);
    restored.open();
    restored.setProcessingTime(0);
    restored.processElement(probe(3, "12:00:01", 24), 0);
    List<DeviceWindows.View> views = drain(restored);
    assertEquals(3, views.get(0).failedTargets, "restored state plus the new probe");
    restored.close();
  }
}
