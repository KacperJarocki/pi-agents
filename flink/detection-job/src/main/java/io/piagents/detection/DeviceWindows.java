package io.piagents.detection;

import java.io.Serializable;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/**
 * Rolling views of one device's recent flows, one per horizon (e.g. 60 s and
 * 900 s). Each horizon keeps its contributions in event-time order and
 * maintains its aggregates incrementally, so adding a flow or expiring old
 * ones costs O(changed contributions), not a rescan.
 *
 * <p>A horizon covers the half-open interval {@code (clock - horizon, clock]},
 * where the clock is the device clock supplied by the caller.
 */
public class DeviceWindows implements Serializable {
  private static final long serialVersionUID = 1L;

  /** AttemptOutcome numbers from the v2 contract that count as failed attempts. */
  static final int OUTCOME_ESTABLISHED = 2;
  static final int OUTCOME_REJECTED = 3;
  static final int OUTCOME_UNANSWERED = 4;

  /** One flow's contribution to the views. */
  public static final class Contribution implements Serializable {
    private static final long serialVersionUID = 1L;
    final long eventTimeMs;
    final String remoteIp;
    final String target;
    final long bytes;
    final boolean failed;

    public Contribution(long eventTimeMs, String remoteIp, String target, long bytes, boolean failed) {
      this.eventTimeMs = eventTimeMs;
      this.remoteIp = remoteIp;
      this.target = target;
      this.bytes = bytes;
      this.failed = failed;
    }

    static Contribution of(FlowRecord r) {
      boolean failed =
          r.attemptOutcome == OUTCOME_REJECTED || r.attemptOutcome == OUTCOME_UNANSWERED;
      String target = r.protocol + "/" + r.remoteIp + "/" + r.remotePort;
      return new Contribution(
          r.eventTimeMs, r.remoteIp, target, r.bytesFromDevice + r.bytesToDevice, failed);
    }
  }

  /** Latest outcome of one (protocol, remote IP, remote port) target. */
  private static final class Target implements Serializable {
    private static final long serialVersionUID = 1L;
    int refs;
    long latestTimeMs = Long.MIN_VALUE;
    boolean latestFailed;
  }

  /** One horizon's contributions and aggregates. */
  static final class Horizon implements Serializable {
    private static final long serialVersionUID = 1L;
    final long lengthMs;
    final ArrayDeque<Contribution> contributions = new ArrayDeque<>();
    final Map<String, Integer> remoteIps = new HashMap<>();
    final Map<String, Target> targets = new HashMap<>();
    long bytes;
    int failedTargets;

    Horizon(long lengthMs) {
      this.lengthMs = lengthMs;
    }

    void add(Contribution c) {
      // Contributions arrive almost in order: insert from the tail.
      if (contributions.isEmpty() || contributions.peekLast().eventTimeMs <= c.eventTimeMs) {
        contributions.addLast(c);
      } else {
        List<Contribution> newer = new ArrayList<>();
        while (!contributions.isEmpty() && contributions.peekLast().eventTimeMs > c.eventTimeMs) {
          newer.add(contributions.pollLast());
        }
        contributions.addLast(c);
        for (int i = newer.size() - 1; i >= 0; i--) {
          contributions.addLast(newer.get(i));
        }
      }
      bytes += c.bytes;
      remoteIps.merge(c.remoteIp, 1, Integer::sum);
      Target t = targets.computeIfAbsent(c.target, k -> new Target());
      t.refs++;
      if (c.eventTimeMs >= t.latestTimeMs) {
        boolean wasFailed = t.refs > 1 && t.latestFailed;
        t.latestTimeMs = c.eventTimeMs;
        t.latestFailed = c.failed;
        failedTargets += (c.failed ? 1 : 0) - (wasFailed ? 1 : 0);
      }
    }

    /** Removes contributions at or before {@code clock - length}; true if any. */
    boolean expire(long clockMs) {
      long cutoff = clockMs - lengthMs;
      boolean changed = false;
      while (!contributions.isEmpty() && contributions.peekFirst().eventTimeMs <= cutoff) {
        Contribution c = contributions.pollFirst();
        changed = true;
        bytes -= c.bytes;
        remoteIps.computeIfPresent(c.remoteIp, (k, n) -> n == 1 ? null : n - 1);
        Target t = targets.get(c.target);
        if (t != null && --t.refs == 0) {
          // The latest contribution of a target expires last (time order).
          if (t.latestFailed) {
            failedTargets--;
          }
          targets.remove(c.target);
        }
      }
      return changed;
    }

    View view(String deviceKey, long clockMs) {
      View v = new View();
      v.deviceKey = deviceKey;
      v.horizonSeconds = (int) (lengthMs / 1000);
      v.windowEndMs = clockMs;
      v.flows = contributions.size();
      v.bytes = bytes;
      v.distinctRemoteIps = remoteIps.size();
      v.distinctTargets = targets.size();
      v.failedTargets = failedTargets;
      return v;
    }
  }

  /** A horizon's aggregates at one device clock value. */
  public static class View {
    public String deviceKey = "";
    public int horizonSeconds;
    public long windowEndMs;
    public int flows;
    public long bytes;
    public int distinctRemoteIps;
    public int distinctTargets;
    /** Distinct targets whose latest attempt was rejected or unanswered. */
    public int failedTargets;

    public View() {}

    /** True when the aggregates (not the window end) differ. */
    boolean sameCounts(View o) {
      return o != null
          && flows == o.flows
          && bytes == o.bytes
          && distinctRemoteIps == o.distinctRemoteIps
          && distinctTargets == o.distinctTargets
          && failedTargets == o.failedTargets;
    }

    @Override
    public String toString() {
      return "View{" + horizonSeconds + "s flows=" + flows + " failedTargets=" + failedTargets
          + " remoteIps=" + distinctRemoteIps + " end=" + windowEndMs + "}";
    }
  }

  private final List<Horizon> horizons = new ArrayList<>();
  private long clockMs = Long.MIN_VALUE;

  public DeviceWindows(long... horizonLengthsMs) {
    for (long length : horizonLengthsMs) {
      horizons.add(new Horizon(length));
    }
  }

  long[] horizonLengths() {
    long[] lengths = new long[horizons.size()];
    for (int i = 0; i < lengths.length; i++) {
      lengths[i] = horizons.get(i).lengthMs;
    }
    return lengths;
  }

  /** Rebuilds state from serialized contributions (already in time order). */
  void restore(long clockMs, List<List<Contribution>> contributions) {
    this.clockMs = clockMs;
    for (int i = 0; i < horizons.size(); i++) {
      for (Contribution c : contributions.get(i)) {
        horizons.get(i).add(c);
      }
    }
  }

  public long clockMs() {
    return clockMs;
  }

  public boolean isEmpty() {
    for (Horizon h : horizons) {
      if (!h.contributions.isEmpty()) {
        return false;
      }
    }
    return true;
  }

  /** Total contributions held, for state-size checks. */
  public int size() {
    int n = 0;
    for (Horizon h : horizons) {
      n += h.contributions.size();
    }
    return n;
  }

  /**
   * Adds a flow. It joins every horizon whose window still covers its event
   * time; the clock moves forward to the event time if it is newer.
   */
  public void add(FlowRecord record) {
    Contribution c = Contribution.of(record);
    advanceTo(Math.max(clockMs, c.eventTimeMs));
    for (Horizon h : horizons) {
      if (c.eventTimeMs > clockMs - h.lengthMs) {
        h.add(c);
      }
    }
  }

  /** Moves the clock forward (never back) and expires old contributions. */
  public boolean advanceTo(long newClockMs) {
    if (newClockMs <= clockMs) {
      return false;
    }
    clockMs = newClockMs;
    boolean changed = false;
    for (Horizon h : horizons) {
      changed |= h.expire(clockMs);
    }
    return changed;
  }

  public List<View> views(String deviceKey) {
    List<View> views = new ArrayList<>(horizons.size());
    for (Horizon h : horizons) {
      views.add(h.view(deviceKey, clockMs));
    }
    return views;
  }

  /** Iterates horizons, for tests. */
  Iterator<Horizon> horizons() {
    return horizons.iterator();
  }
}
