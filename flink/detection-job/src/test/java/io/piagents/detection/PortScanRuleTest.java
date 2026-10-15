package io.piagents.detection;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

/**
 * Runs flows through {@link DeviceWindows} and {@link PortScanRule} on a
 * simulated clock (wall time == event time, as for a live device).
 */
class PortScanRuleTest {
  private static final long T0 = Events.DAY_MS + 12 * 3_600_000L;

  /** Feeds flows and silence; collects transitions. */
  private static final class Sim {
    final DeviceWindows windows = new DeviceWindows(60_000, 900_000);
    final PortScanRule rule = new PortScanRule(new PortScanRule.Config());
    final RuleState state = new RuleState();
    final List<Transition> out = new ArrayList<>();
    int seq;

    void flow(long t, int port, int outcome) {
      FlowRecord r = new FlowRecord();
      r.eventId = "gw/" + seq++;
      r.deviceKey = Events.DEVICE;
      r.eventTimeMs = t;
      r.ingestTimeMs = t + 3;
      r.remoteIp = "203.0.113.10";
      r.remotePort = port;
      r.protocol = 1;
      r.attemptOutcome = outcome;
      windows.add(r);
      publish(t);
    }

    void probe(long t, int port) {
      flow(t, port, DeviceWindows.OUTCOME_UNANSWERED);
    }

    void publish(long now) {
      DeviceWindows.ViewSet set = new DeviceWindows.ViewSet();
      set.deviceKey = Events.DEVICE;
      set.windowEndMs = windows.clockMs();
      set.views = windows.views(Events.DEVICE).toArray(new DeviceWindows.View[0]);
      out.addAll(rule.onViews(state, set, now));
    }

    /** Silence until {@code until}, ticking every 5 s like the timers. */
    void silence(long until) {
      for (long t = windows.clockMs() + 5_000; t <= until; t += 5_000) {
        windows.advanceTo(t);
        publish(t);
        out.addAll(rule.onClock(state, t));
      }
    }

    List<String> statuses() {
      return out.stream().map(Transition::statusName).collect(Collectors.toList());
    }
  }

  @Test
  void fastScanOpensThroughTheShortHorizon() {
    Sim sim = new Sim();
    for (int i = 0; i < 25; i++) {
      sim.probe(T0 + i * 200L, 1000 + i);
    }
    Transition open = sim.out.get(0);
    assertEquals("OPEN", open.statusName());
    assertTrue(open.overThreshold[0], "60 s over threshold");
    assertEquals(20, open.values[0]);
    assertEquals(T0 + 19 * 200L, open.evidenceEventTimeMs, "the 20th target completed the evidence");
    assertEquals(T0 + 19 * 200L + 3, open.evidenceIngestTimeMs);
    assertEquals(open.incidentId + ":1", open.transitionId());
    assertEquals(20, open.sampleEventIds.length);
    assertEquals("gw/19", open.sampleEventIds[0], "newest evidence first");
  }

  @Test
  void slowScanOpensThroughTheLongHorizonOnly() {
    Sim sim = new Sim();
    for (int i = 0; i < 45; i++) {
      sim.probe(T0 + i * 20_000L, 2000 + i); // one probe every 20 s
    }
    Transition open = sim.out.get(0);
    assertEquals("OPEN", open.statusName());
    assertFalse(open.overThreshold[0]);
    assertTrue(open.overThreshold[1]);
    assertTrue(open.values[0] <= 3);
    assertEquals(40, open.values[1]);
  }

  @Test
  void benignTrafficOpensNothing() {
    Sim sim = new Sim();
    for (int i = 0; i < 200; i++) {
      sim.flow(T0 + i * 1_000L, 3000 + i, DeviceWindows.OUTCOME_ESTABLISHED);
    }
    for (int i = 0; i < 10; i++) {
      sim.probe(T0 + i * 3_000L, 8000 + i); // a few failures (e.g. dead servers)
    }
    sim.silence(T0 + 1_200_000);
    assertTrue(sim.out.isEmpty(), sim.statuses().toString());
  }

  @Test
  void oscillationAroundTheThresholdIsOneIncident() {
    Sim sim = new Sim();
    long t = T0;
    int port = 4000;
    // Keep the 60 s signal hovering around 20 for five minutes.
    for (int i = 0; i < 300; i++) {
      t += 3_000;
      sim.probe(t, port++);
    }
    sim.silence(t + 1_000_000);
    long incidents = sim.out.stream().map(x -> x.incidentId).distinct().count();
    assertEquals(1, incidents);
    assertEquals("OPEN", sim.statuses().get(0));
    assertEquals("RESOLVE", sim.statuses().get(sim.statuses().size() - 1));
    assertFalse(sim.statuses().contains("REOPEN"));
  }

  @Test
  void resolvesOnlyAfterTheHoldOnceBothHorizonsAreClear() {
    Sim sim = new Sim();
    for (int i = 0; i < 25; i++) {
      sim.probe(T0 + i * 200L, 1000 + i);
    }
    sim.silence(T0 + 1_100_000);
    Transition resolve = sim.out.get(sim.out.size() - 1);
    assertEquals("RESOLVE", resolve.statusName());
    // The 900 s view drops to ≤ 20 failed targets once the probes age out
    // (~T0 + 900 s); the hold adds 60 s.
    assertTrue(resolve.evidenceEventTimeMs >= T0 + 900_000 + 60_000, "" + resolve.evidenceEventTimeMs);
    assertEquals(sim.out.get(0).incidentId, resolve.incidentId);
  }

  @Test
  void crossingWithinTheCooldownReopensAndLaterOpensANewIncident() {
    Sim sim = new Sim();
    for (int i = 0; i < 25; i++) {
      sim.probe(T0 + i * 200L, 1000 + i);
    }
    sim.silence(T0 + 1_000_000);
    String first = sim.out.get(0).incidentId;
    long resolvedAt = sim.out.get(sim.out.size() - 1).evidenceEventTimeMs;

    long t = resolvedAt + 60_000; // within the 300 s cooldown
    for (int i = 0; i < 25; i++) {
      sim.probe(t + i * 200L, 5000 + i);
    }
    Transition reopen = sim.out.stream().filter(x -> x.status == Transition.REOPEN).findFirst().orElseThrow();
    assertEquals(first, reopen.incidentId);

    sim.silence(t + 2_000_000);
    long later = sim.windows.clockMs() + 10_000; // well after the cooldown
    for (int i = 0; i < 25; i++) {
      sim.probe(later + i * 200L, 6000 + i);
    }
    Transition last = sim.out.get(sim.out.size() - 1);
    assertEquals("OPEN", last.statusName());
    assertNotEquals(first, last.incidentId);
  }

  @Test
  void revisionsOnlyOnMeaningfulChange() {
    Sim sim = new Sim();
    for (int i = 0; i < 30; i++) {
      sim.probe(T0 + i * 200L, 1000 + i); // 21..30 change the count but not the bucket
    }
    assertEquals(List.of("OPEN"), sim.statuses());
    for (int i = 30; i < 45; i++) {
      sim.probe(T0 + i * 200L, 1000 + i); // 40 in 60 s: medium severity, 900 s over too
    }
    assertEquals(List.of("OPEN", "UPDATE"), sim.statuses());
    Transition update = sim.out.get(1);
    assertEquals(2, update.revision);
    assertEquals(2, update.severity);
    assertTrue(update.overThreshold[0] && update.overThreshold[1]);
  }

  @Test
  void replayFromScratchYieldsIdenticalIds() {
    List<String> runs = new ArrayList<>();
    for (int run = 0; run < 2; run++) {
      Sim sim = new Sim();
      for (int i = 0; i < 25; i++) {
        sim.probe(T0 + i * 200L, 1000 + i);
      }
      sim.silence(T0 + 1_000_000);
      runs.add(sim.out.stream().map(Transition::transitionId).collect(Collectors.joining(",")));
    }
    assertEquals(runs.get(0), runs.get(1));
    assertArrayEquals(
        new String[] {PortScanRule.incidentId(Events.DEVICE, T0 + 19 * 200L)},
        new String[] {runs.get(0).split(":")[0]});
  }
}
