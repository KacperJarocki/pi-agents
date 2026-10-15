package io.piagents.detection;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

class DeviceWindowsTest {
  private static final long T0 = Events.DAY_MS + 12 * 3_600_000L;

  private static DeviceWindows windows() {
    return new DeviceWindows(60_000, 900_000);
  }

  private static FlowRecord flow(long timeMs, String ip, int port, int outcome) {
    FlowRecord r = new FlowRecord();
    r.eventId = "gw/" + timeMs + "/" + port;
    r.deviceKey = Events.DEVICE;
    r.eventTimeMs = timeMs;
    r.remoteIp = ip;
    r.remotePort = port;
    r.protocol = 1;
    r.attemptOutcome = outcome;
    r.bytesFromDevice = 60;
    return r;
  }

  private static FlowRecord probe(long timeMs, int port) {
    return flow(timeMs, "203.0.113.10", port, DeviceWindows.OUTCOME_UNANSWERED);
  }

  private static DeviceWindows.View view(DeviceWindows w, int horizon) {
    List<DeviceWindows.View> views = w.views(Events.DEVICE);
    return views.get(horizon);
  }

  @Test
  void burstFillsBothHorizons() {
    DeviceWindows w = windows();
    for (int i = 0; i < 100; i++) {
      w.add(probe(T0 + i * 100L, 1000 + i));
    }
    for (int h = 0; h < 2; h++) {
      assertEquals(100, view(w, h).flows);
      assertEquals(100, view(w, h).failedTargets);
      assertEquals(1, view(w, h).distinctRemoteIps);
      assertEquals(6000, view(w, h).bytes);
    }
  }

  @Test
  void sparseDeviceOnlyAccumulatesInTheLongHorizon() {
    DeviceWindows w = windows();
    for (int i = 0; i < 20; i++) {
      w.add(probe(T0 + i * 120_000L, 2000 + i)); // one probe every 2 minutes
    }
    assertEquals(1, view(w, 0).failedTargets, "60 s sees only the latest probe");
    assertEquals(8, view(w, 1).failedTargets, "900 s sees probes from the last 15 minutes");
  }

  @Test
  void outOfOrderEventIsInsertedAndExpiresInTimeOrder() {
    DeviceWindows w = windows();
    w.add(probe(T0 + 10_000, 22));
    w.add(probe(T0 + 30_000, 23));
    w.add(probe(T0 + 20_000, 24)); // 10 s late, inside the lateness allowance
    assertEquals(3, view(w, 0).failedTargets);

    w.advanceTo(T0 + 75_000); // 60 s window is (T0+15s, T0+75s]
    assertEquals(2, view(w, 0).flows);
    w.advanceTo(T0 + 85_000); // (T0+25s, T0+85s]
    assertEquals(1, view(w, 0).flows);
    assertEquals(3, view(w, 1).flows);
  }

  @Test
  void eventOlderThanAHorizonOnlyJoinsTheLongerOne() {
    DeviceWindows w = windows();
    w.add(probe(T0 + 120_000, 22));
    w.add(probe(T0 + 50_000, 23)); // 70 s behind the clock
    assertEquals(1, view(w, 0).flows);
    assertEquals(2, view(w, 1).flows);
  }

  @Test
  void silenceExpiresEvidenceHorizonByHorizon() {
    DeviceWindows w = windows();
    w.add(probe(T0, 22));
    assertTrue(w.advanceTo(T0 + 60_000));
    assertEquals(0, view(w, 0).flows);
    assertEquals(1, view(w, 1).flows);
    w.advanceTo(T0 + 900_000);
    assertTrue(w.isEmpty());
    assertEquals(0, view(w, 1).failedTargets);
  }

  @Test
  void laterEstablishedAttemptRetractsTheTarget() {
    DeviceWindows w = windows();
    w.add(probe(T0, 443));
    assertEquals(1, view(w, 0).failedTargets);
    w.add(flow(T0 + 1_500, "203.0.113.10", 443, DeviceWindows.OUTCOME_ESTABLISHED));
    assertEquals(0, view(w, 0).failedTargets);
    assertEquals(1, view(w, 0).distinctTargets);
  }

  @Test
  void establishedEvidenceThatArrivesLateDoesNotOverrideNewerFailure() {
    DeviceWindows w = windows();
    w.add(probe(T0 + 5_000, 80));
    w.add(flow(T0 + 1_000, "203.0.113.10", 80, DeviceWindows.OUTCOME_ESTABLISHED));
    assertEquals(1, view(w, 0).failedTargets);
  }

  @Test
  void longRunningStreamKeepsStateBoundedByTheHorizons() {
    DeviceWindows w = windows();
    int perSecond = 10;
    int maxSize = 0;
    for (int s = 0; s < 3_600; s++) {
      for (int i = 0; i < perSecond; i++) {
        w.add(probe(T0 + s * 1_000L + i * 100L, (s * perSecond + i) % 60_000));
      }
      maxSize = Math.max(maxSize, w.size());
    }
    assertTrue(maxSize <= (60 + 900) * perSecond, "size " + maxSize);
    assertEquals(60 * perSecond, view(w, 0).flows);
    assertEquals(900 * perSecond, view(w, 1).flows);
  }

  @Test
  void serializerRoundTripPreservesViews() throws Exception {
    DeviceWindows w = windows();
    w.add(probe(T0, 22));
    w.add(probe(T0 + 1_000, 23));
    w.add(flow(T0 + 2_000, "198.51.100.1", 443, DeviceWindows.OUTCOME_ESTABLISHED));
    var out = new org.apache.flink.core.memory.DataOutputSerializer(256);
    DeviceWindowsSerializer.INSTANCE.serialize(w, out);
    DeviceWindows copy =
        DeviceWindowsSerializer.INSTANCE.deserialize(
            new org.apache.flink.core.memory.DataInputDeserializer(out.getCopyOfBuffer()));
    for (int h = 0; h < 2; h++) {
      assertTrue(view(w, h).sameCounts(view(copy, h)));
    }
    assertEquals(w.clockMs(), copy.clockMs());
  }
}
