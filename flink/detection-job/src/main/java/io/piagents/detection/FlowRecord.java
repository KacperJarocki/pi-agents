package io.piagents.detection;

import io.piagents.events.v2.FlowEvent;
import java.util.Arrays;

/**
 * A flow event as it travels through the job. A Flink POJO (public fields,
 * no-arg constructor) so state and network use the POJO serializer instead of
 * Kryo. {@code raw} keeps the original bytes for the late-event topic.
 */
public class FlowRecord {
  public String eventId = "";
  public String deviceKey = "";
  public long eventTimeMs;
  public long ingestTimeMs;
  public long captureSequence;
  public String remoteIp = "";
  public int remotePort;
  /** {@code Protocol} enum number from the v2 contract. */
  public int protocol;
  /** {@code Direction} enum number from the v2 contract. */
  public int direction;
  /** {@code AttemptOutcome} enum number from the v2 contract. */
  public int attemptOutcome;
  public boolean continuation;
  public long bytesFromDevice;
  public long bytesToDevice;
  /** Set by the deserializer when the bytes are not a usable FlowEvent. */
  public String malformedReason;
  public byte[] raw = new byte[0];

  public FlowRecord() {}

  public static FlowRecord fromEvent(FlowEvent event, byte[] raw) {
    FlowRecord r = new FlowRecord();
    r.eventId = event.getEnvelope().getEventId();
    r.deviceKey = event.getEnvelope().getDeviceKey();
    r.eventTimeMs = event.getEnvelope().getEventTimeUnixMillis();
    r.ingestTimeMs = event.getEnvelope().getIngestTimeUnixMillis();
    r.captureSequence = event.getCaptureSequence();
    boolean outbound = event.getDirectionValue() != 2;
    r.remoteIp = outbound ? event.getDstIp() : event.getSrcIp();
    r.remotePort = outbound ? event.getDstPort() : event.getSrcPort();
    r.protocol = event.getProtocolValue();
    r.direction = event.getDirectionValue();
    r.attemptOutcome = event.getAttemptOutcomeValue();
    r.continuation = event.getContinuation();
    r.bytesFromDevice = event.getBytesFromDevice();
    r.bytesToDevice = event.getBytesToDevice();
    r.raw = raw;
    return r;
  }

  public static FlowRecord malformed(String reason, byte[] raw) {
    FlowRecord r = new FlowRecord();
    r.malformedReason = reason;
    r.raw = raw == null ? new byte[0] : raw;
    return r;
  }

  @Override
  public String toString() {
    return "FlowRecord{" + eventId + " @" + eventTimeMs + " " + deviceKey + "}";
  }

  @Override
  public boolean equals(Object o) {
    if (!(o instanceof FlowRecord other)) {
      return false;
    }
    return eventId.equals(other.eventId)
        && eventTimeMs == other.eventTimeMs
        && Arrays.equals(raw, other.raw);
  }

  @Override
  public int hashCode() {
    return eventId.hashCode();
  }
}
