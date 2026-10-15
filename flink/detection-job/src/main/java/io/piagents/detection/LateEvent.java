package io.piagents.detection;

/** An event routed to {@code traffic.late.v2}, with the reason. */
public class LateEvent {
  public static final String MALFORMED = "malformed";
  public static final String TOO_LATE = "too_late";

  public String reason = "";
  public String detail = "";
  public String deviceKey = "";
  public String eventId = "";
  public long eventTimeMs;
  /** The device clock when the event was classified (too-late events). */
  public long deviceClockMs;
  public byte[] raw = new byte[0];

  public LateEvent() {}

  static LateEvent of(String reason, String detail, FlowRecord record, long deviceClockMs) {
    LateEvent e = new LateEvent();
    e.reason = reason;
    e.detail = detail == null ? "" : detail;
    e.deviceKey = record.deviceKey;
    e.eventId = record.eventId;
    e.eventTimeMs = record.eventTimeMs;
    e.deviceClockMs = deviceClockMs;
    e.raw = record.raw;
    return e;
  }

  @Override
  public String toString() {
    return "LateEvent{" + reason + " " + eventId + " @" + eventTimeMs + "}";
  }
}
