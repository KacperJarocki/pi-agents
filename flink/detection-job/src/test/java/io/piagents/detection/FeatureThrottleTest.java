package io.piagents.detection;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.piagents.events.v2.FeatureUpdate;
import java.util.ArrayList;
import java.util.List;
import org.apache.flink.api.common.typeinfo.Types;
import org.apache.flink.streaming.api.operators.KeyedProcessOperator;
import org.apache.flink.streaming.runtime.streamrecord.StreamRecord;
import org.apache.flink.streaming.util.KeyedOneInputStreamOperatorTestHarness;
import org.junit.jupiter.api.Test;

class FeatureThrottleTest {
  private static DeviceWindows.ViewSet set(long clock, int failed) {
    DeviceWindows.ViewSet s = new DeviceWindows.ViewSet();
    s.deviceKey = Events.DEVICE;
    s.windowEndMs = clock;
    DeviceWindows.View a = new DeviceWindows.View();
    a.deviceKey = Events.DEVICE;
    a.horizonSeconds = 60;
    a.windowEndMs = clock;
    a.failedTargets = failed;
    DeviceWindows.View b = new DeviceWindows.View();
    b.deviceKey = Events.DEVICE;
    b.horizonSeconds = 900;
    b.windowEndMs = clock;
    b.failedTargets = failed;
    s.views = new DeviceWindows.View[] {a, b};
    return s;
  }

  @Test
  void atMostOneUpdatePerSecondPerHorizonAndTheLatestIsKept() throws Exception {
    var h =
        new KeyedOneInputStreamOperatorTestHarness<String, DeviceWindows.ViewSet, DeviceWindows.View>(
            new KeyedProcessOperator<>(new FeatureThrottle(1_000)), s -> s.deviceKey, Types.STRING);
    h.open();
    h.setProcessingTime(0);
    for (int i = 1; i <= 50; i++) {
      h.setProcessingTime(i * 20L); // 50 changes within one second
      h.processElement(set(i, i), 0);
    }
    h.setProcessingTime(1_100);
    List<DeviceWindows.View> out = new ArrayList<>();
    for (Object o : h.getOutput()) {
      if (o instanceof StreamRecord<?> r) {
        out.add((DeviceWindows.View) r.getValue());
      }
    }
    assertEquals(4, out.size(), "first change + latest after the interval, per horizon");
    assertEquals(50, out.get(3).failedTargets);

    FeatureUpdate proto = FeatureUpdateSerializer.toProto(out.get(3));
    assertEquals(900, proto.getHorizonSeconds());
    assertEquals(50.0, proto.getFeaturesOrThrow("failed_targets"));
    assertEquals(Events.DEVICE + "/900/50", proto.getEnvelope().getEventId());
    h.close();
  }
}
