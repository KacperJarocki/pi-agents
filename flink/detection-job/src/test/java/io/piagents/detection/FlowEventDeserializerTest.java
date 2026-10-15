package io.piagents.detection;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

import io.piagents.events.v2.FlowEvent;
import org.junit.jupiter.api.Test;

class FlowEventDeserializerTest {
  private final FlowEventDeserializer deserializer = new FlowEventDeserializer();

  @Test
  void decodesIdentityTimesAndRemoteEndpoint() {
    byte[] bytes = Events.flow(41, Events.at("12:00:01"), 23).toByteArray();
    FlowRecord r = deserializer.deserialize(bytes);

    assertNull(r.malformedReason);
    assertEquals("gw/41", r.eventId);
    assertEquals(Events.DEVICE, r.deviceKey);
    assertEquals(Events.at("12:00:01"), r.eventTimeMs);
    assertEquals(41, r.captureSequence);
    assertEquals("203.0.113.10", r.remoteIp);
    assertEquals(23, r.remotePort);
    assertEquals(4, r.attemptOutcome);
    assertArrayEquals(bytes, r.raw);
  }

  @Test
  void inboundFlowsUseTheSourceAsRemoteEndpoint() {
    FlowEvent inbound =
        Events.flow(1, Events.at("12:00:00"), 40000).toBuilder()
            .setSrcIp("198.51.100.7")
            .setSrcPort(5555)
            .setDstIp("192.168.50.10")
            .setDirection(io.piagents.events.v2.Direction.DIRECTION_INBOUND)
            .build();
    FlowRecord r = deserializer.deserialize(inbound.toByteArray());
    assertEquals("198.51.100.7", r.remoteIp);
    assertEquals(5555, r.remotePort);
  }

  @Test
  void garbageAndIncompleteEventsAreMalformedNotFatal() {
    assertNotNull(deserializer.deserialize(new byte[] {(byte) 0xff, 0x01}).malformedReason);
    assertNotNull(deserializer.deserialize(null).malformedReason);
    FlowEvent noEnvelope = FlowEvent.newBuilder().setDstPort(80).build();
    assertNotNull(deserializer.deserialize(noEnvelope.toByteArray()).malformedReason);
  }
}
