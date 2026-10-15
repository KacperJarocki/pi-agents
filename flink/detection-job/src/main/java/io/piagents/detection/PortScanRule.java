package io.piagents.detection;

import java.io.Serializable;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;

/**
 * Port-scan rule v1. The signal per horizon is the number of distinct
 * targets whose latest attempt was rejected or unanswered.
 *
 * <ul>
 *   <li>Open when any horizon reaches its open threshold (60 s ≥ 20 or 900 s ≥ 40 by default).
 *   <li>Resolve when every horizon is at or below {@code clearFraction} of its threshold for
 *       {@code holdMs} of device time.
 *   <li>A new crossing within {@code cooldownMs} after a resolve reopens the same incident.
 *   <li>Revisions are emitted only when the status, the set of horizons over threshold or the
 *       severity bucket changes.
 * </ul>
 *
 * <p>The incident ID is derived from the device key, the threat family and the event time of
 * the episode's first threshold crossing, so replaying the same events yields the same IDs.
 */
public final class PortScanRule implements Serializable {
  private static final long serialVersionUID = 1L;

  public static final String RULE_ID = "port-scan";
  public static final String RULE_VERSION = "v1";
  static final String FAMILY = "port_scan";

  /** Rule parameters; defaults are the v1 values from the design. */
  public static final class Config implements Serializable {
    private static final long serialVersionUID = 1L;
    public int[] horizonSeconds = {60, 900};
    public double[] openThresholds = {20, 40};
    public double clearFraction = 0.5;
    public long holdMs = 60_000;
    public long cooldownMs = 300_000;
    /** Signal / threshold ratios for medium and high severity. */
    public double mediumRatio = 2.0;
    public double highRatio = 4.0;
  }

  private final Config config;

  public PortScanRule(Config config) {
    this.config = config;
  }

  public Config config() {
    return config;
  }

  /** Applies the latest views of every horizon and evaluates the rule once. */
  public List<Transition> onViews(RuleState s, DeviceWindows.ViewSet set, long nowMs) {
    long ingest = 0;
    for (int h = 0; h < set.views.length && h < 2; h++) {
      s.setView(h, set.views[h]);
      ingest = Math.max(ingest, set.views[h].latestIngestTimeMs);
    }
    if (set.windowEndMs > s.clockMs) {
      s.clockMs = set.windowEndMs;
      s.clockWallMs = nowMs;
    }
    return evaluate(s, s.clockMs, ingest, nowMs);
  }

  /** Re-evaluates during silence: the device clock advanced by wall time. */
  public List<Transition> onClock(RuleState s, long nowMs) {
    if (s.clockMs == Long.MIN_VALUE) {
      return List.of();
    }
    long clock = s.clockMs + Math.max(0, nowMs - s.clockWallMs);
    return evaluate(s, clock, 0, nowMs);
  }

  private double value(RuleState s, int horizon) {
    DeviceWindows.View v = s.view(horizon);
    return v == null ? 0 : v.failedTargets;
  }

  private int overMask(RuleState s) {
    int mask = 0;
    for (int h = 0; h < config.openThresholds.length; h++) {
      if (value(s, h) >= config.openThresholds[h]) {
        mask |= 1 << h;
      }
    }
    return mask;
  }

  private int severity(RuleState s) {
    double ratio = 0;
    for (int h = 0; h < config.openThresholds.length; h++) {
      ratio = Math.max(ratio, value(s, h) / config.openThresholds[h]);
    }
    if (ratio >= config.highRatio) {
      return 3;
    }
    return ratio >= config.mediumRatio ? 2 : 1;
  }

  private boolean clear(RuleState s) {
    for (int h = 0; h < config.openThresholds.length; h++) {
      if (value(s, h) > config.clearFraction * config.openThresholds[h]) {
        return false;
      }
    }
    return true;
  }

  private List<Transition> evaluate(RuleState s, long clockMs, long ingestMs, long nowMs) {
    List<Transition> out = new ArrayList<>(1);
    int mask = overMask(s);
    if (s.status != RuleState.ACTIVE) {
      if (mask == 0) {
        return out;
      }
      int status;
      if (s.status == RuleState.RESOLVED && clockMs - s.resolvedAtMs < config.cooldownMs) {
        status = Transition.REOPEN;
        s.revision++;
      } else {
        status = Transition.OPEN;
        s.episodeStartMs = clockMs;
        s.incidentId = incidentId(deviceKey(s), clockMs);
        s.revision = 1;
      }
      s.status = RuleState.ACTIVE;
      s.overMask = mask;
      s.severity = severity(s);
      s.clearSinceMs = Long.MIN_VALUE;
      out.add(transition(s, status, clockMs, ingestMs, nowMs));
      return out;
    }

    if (clear(s)) {
      if (s.clearSinceMs == Long.MIN_VALUE) {
        s.clearSinceMs = clockMs;
      }
      if (clockMs - s.clearSinceMs >= config.holdMs) {
        s.revision++;
        s.status = RuleState.RESOLVED;
        s.resolvedAtMs = clockMs;
        s.overMask = mask;
        s.severity = severity(s);
        out.add(transition(s, Transition.RESOLVE, clockMs, ingestMs, nowMs));
        return out;
      }
    } else {
      s.clearSinceMs = Long.MIN_VALUE;
    }
    int severity = severity(s);
    if (mask != s.overMask || severity != s.severity) {
      s.revision++;
      s.overMask = mask;
      s.severity = severity;
      out.add(transition(s, Transition.UPDATE, clockMs, ingestMs, nowMs));
    }
    return out;
  }

  /** Whether the state can be dropped: nothing active and the cooldown is over. */
  public boolean idle(RuleState s, long clockMs) {
    boolean noEvidence =
        (s.shortView == null || s.shortView.flows == 0)
            && (s.longView == null || s.longView.flows == 0);
    return switch (s.status) {
      case RuleState.NONE -> noEvidence;
      case RuleState.RESOLVED -> noEvidence && clockMs - s.resolvedAtMs >= config.cooldownMs;
      default -> false;
    };
  }

  private static String deviceKey(RuleState s) {
    if (s.shortView != null) {
      return s.shortView.deviceKey;
    }
    return s.longView == null ? "" : s.longView.deviceKey;
  }

  static String incidentId(String deviceKey, long episodeStartMs) {
    try {
      MessageDigest sha = MessageDigest.getInstance("SHA-256");
      byte[] digest =
          sha.digest(
              (deviceKey + "|" + FAMILY + "|" + episodeStartMs).getBytes(StandardCharsets.UTF_8));
      return HexFormat.of().formatHex(digest, 0, 16);
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException("SHA-256 is always available", e);
    }
  }

  private Transition transition(RuleState s, int status, long clockMs, long ingestMs, long nowMs) {
    int n = config.openThresholds.length;
    Transition t = new Transition();
    t.deviceKey = deviceKey(s);
    t.incidentId = s.incidentId;
    t.revision = s.revision;
    t.status = status;
    t.severity = s.severity;
    t.ruleId = RULE_ID;
    t.ruleVersion = RULE_VERSION;
    t.episodeStartMs = s.episodeStartMs;
    t.evidenceEventTimeMs = clockMs;
    t.evidenceIngestTimeMs = ingestMs;
    t.detectedAtMs = nowMs;
    t.horizonSeconds = new int[n];
    t.values = new double[n];
    t.openThresholds = new double[n];
    t.overThreshold = new boolean[n];
    String[] sample = new String[0];
    for (int h = 0; h < n; h++) {
      t.horizonSeconds[h] = config.horizonSeconds[h];
      t.values[h] = value(s, h);
      t.openThresholds[h] = config.openThresholds[h];
      t.overThreshold[h] = (s.overMask & (1 << h)) != 0;
      DeviceWindows.View v = s.view(h);
      if (v != null && v.sampleEventIds.length > sample.length) {
        sample = v.sampleEventIds;
      }
    }
    t.sampleEventIds = sample;
    return t;
  }
}
