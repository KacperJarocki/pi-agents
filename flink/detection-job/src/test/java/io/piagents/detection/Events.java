package io.piagents.detection;

import io.piagents.events.v2.AttemptOutcome;
import io.piagents.events.v2.Direction;
import io.piagents.events.v2.Envelope;
import io.piagents.events.v2.FlowEvent;
import io.piagents.events.v2.Protocol;
import java.time.LocalTime;
import java.time.ZoneOffset;

/** Builders for FlowEvents in tests. */
final class Events {
  static final String GATEWAY = "gw";
  static final String DEVICE = "gw:02:00:00:00:00:0a";
  /** 2026-10-15T00:00:00Z. */
  static final long DAY_MS = 1_760_486_400_000L;

  private Events() {}

  static long at(String time) {
    return DAY_MS + LocalTime.parse(time).toSecondOfDay() * 1000L;
  }

  static FlowEvent flow(long sequence, long eventTimeMs, int dstPort) {
    return FlowEvent.newBuilder()
        .setEnvelope(
            Envelope.newBuilder()
                .setEventId(GATEWAY + "/" + sequence)
                .setSchemaVersion("v2")
                .setGatewayId(GATEWAY)
                .setDeviceKey(DEVICE)
                .setEventTimeUnixMillis(eventTimeMs)
                .setIngestTimeUnixMillis(eventTimeMs + 5))
        .setCaptureSequence(sequence)
        .setDeviceMac("02:00:00:00:00:0a")
        .setSrcIp("192.168.50.10")
        .setDstIp("203.0.113.10")
        .setSrcPort(40000)
        .setDstPort(dstPort)
        .setProtocol(Protocol.PROTOCOL_TCP)
        .setDirection(Direction.DIRECTION_OUTBOUND)
        .setFirstPacketTimeUnixMicros(eventTimeMs * 1000)
        .setLastPacketTimeUnixMicros(eventTimeMs * 1000)
        .setPacketsFromDevice(1)
        .setTcpSyn(1)
        .setAttemptOutcome(AttemptOutcome.ATTEMPT_OUTCOME_UNANSWERED)
        .build();
  }

  static FlowRecord record(long sequence, String time) {
    return new FlowEventDeserializer().deserialize(flow(sequence, at(time), 22).toByteArray());
  }

  @SuppressWarnings("unused")
  private static final ZoneOffset UTC = ZoneOffset.UTC;
}
