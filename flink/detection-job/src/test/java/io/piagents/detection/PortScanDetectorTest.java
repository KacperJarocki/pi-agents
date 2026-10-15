package io.piagents.detection;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.ArrayList;
import java.util.List;
import org.apache.flink.api.common.typeinfo.Types;
import org.apache.flink.streaming.api.operators.KeyedProcessOperator;
import org.apache.flink.streaming.runtime.streamrecord.StreamRecord;
import org.apache.flink.streaming.util.KeyedOneInputStreamOperatorTestHarness;
import org.junit.jupiter.api.Test;

/** The detector's timer resolves an incident and drops state without new views. */
class PortScanDetectorTest {

  private static DeviceWindows.ViewSet set(long clockMs, int short60, int long900) {
    DeviceWindows.ViewSet s = new DeviceWindows.ViewSet();
    s.deviceKey = Events.DEVICE;
    s.windowEndMs = clockMs;
    DeviceWindows.View a = new DeviceWindows.View();
    a.deviceKey = Events.DEVICE;
    a.horizonSeconds = 60;
    a.failedTargets = short60;
    a.flows = short60;
    DeviceWindows.View b = new DeviceWindows.View();
    b.deviceKey = Events.DEVICE;
    b.horizonSeconds = 900;
    b.failedTargets = long900;
    b.flows = long900;
    s.views = new DeviceWindows.View[] {a, b};
    return s;
  }

  @Test
  void timerResolvesAfterHoldAndStateIsDroppedAfterCooldown() throws Exception {
    var h =
        new KeyedOneInputStreamOperatorTestHarness<String, DeviceWindows.ViewSet, Transition>(
            new KeyedProcessOperator<>(new PortScanDetector(new PortScanRule.Config())),
            v -> v.deviceKey,
            Types.STRING);
    h.open();
    long t0 = Events.DAY_MS;
    h.setProcessingTime(t0);
    h.processElement(set(t0, 25, 25), 0); // OPEN
    h.setProcessingTime(t0 + 1_000);
    h.processElement(set(t0 + 1_000, 0, 0), 0); // evidence gone: clear starts

    // No more views; only timers advance time.
    for (long t = t0 + 5_000; t <= t0 + 400_000; t += 5_000) {
      h.setProcessingTime(t);
    }
    List<String> statuses = new ArrayList<>();
    for (Object o : h.getOutput()) {
      if (o instanceof StreamRecord<?> r) {
        statuses.add(((Transition) r.getValue()).statusName());
      }
    }
    // Dropping below both thresholds changes the set of horizons over
    // threshold (UPDATE); the hold then resolves it without further views.
    assertEquals(List.of("OPEN", "UPDATE", "RESOLVE"), statuses);
    assertEquals(0, h.numKeyedStateEntries(), "state dropped after the cooldown");
    assertEquals(0, h.numProcessingTimeTimers());
    h.close();
  }
}
