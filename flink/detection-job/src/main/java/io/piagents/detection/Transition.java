package io.piagents.detection;

/** A detection state change, encoded as a v2 DetectionTransition on output. */
public class Transition {
  /** TransitionStatus numbers from the v2 contract. */
  public static final int OPEN = 1;
  public static final int UPDATE = 2;
  public static final int REOPEN = 3;
  public static final int RESOLVE = 4;

  public String deviceKey = "";
  public String incidentId = "";
  public long revision;
  public int status;
  /** Severity numbers from the v2 contract (1 low, 2 medium, 3 high). */
  public int severity;
  public String ruleId = "";
  public String ruleVersion = "";
  public long episodeStartMs;
  public long evidenceEventTimeMs;
  public long evidenceIngestTimeMs;
  public long detectedAtMs;
  public int[] horizonSeconds = new int[0];
  public double[] values = new double[0];
  public double[] openThresholds = new double[0];
  public boolean[] overThreshold = new boolean[0];
  public String[] sampleEventIds = new String[0];

  public Transition() {}

  public String transitionId() {
    return incidentId + ":" + revision;
  }

  @Override
  public String toString() {
    return "Transition{" + statusName() + " " + transitionId() + " sev=" + severity + "}";
  }

  public String statusName() {
    return switch (status) {
      case OPEN -> "OPEN";
      case UPDATE -> "UPDATE";
      case REOPEN -> "REOPEN";
      case RESOLVE -> "RESOLVE";
      default -> "UNSPECIFIED";
    };
  }
}
