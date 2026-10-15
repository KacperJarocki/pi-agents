package io.piagents.detection;

/** Per-device state of the port-scan rule (a Flink POJO). */
public class RuleState {
  public static final int NONE = 0;
  public static final int ACTIVE = 1;
  public static final int RESOLVED = 2;

  public int status = NONE;
  public String incidentId = "";
  public long revision;
  public long episodeStartMs;
  /** Bit i set when horizon i is at or above its open threshold. */
  public int overMask;
  public int severity;
  /** Device clock when the clear condition started to hold, or MIN_VALUE. */
  public long clearSinceMs = Long.MIN_VALUE;
  public long resolvedAtMs;
  /** Latest view per horizon (index 0 short, 1 long). */
  public DeviceWindows.View shortView;
  public DeviceWindows.View longView;
  /** Device clock of the latest view and the wall time it was seen. */
  public long clockMs = Long.MIN_VALUE;
  public long clockWallMs;

  public RuleState() {}

  DeviceWindows.View view(int horizon) {
    return horizon == 0 ? shortView : longView;
  }

  void setView(int horizon, DeviceWindows.View view) {
    if (horizon == 0) {
      shortView = view;
    } else {
      longView = view;
    }
  }
}
