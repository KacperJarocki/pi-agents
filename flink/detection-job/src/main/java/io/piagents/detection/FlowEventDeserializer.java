package io.piagents.detection;

import com.google.protobuf.InvalidProtocolBufferException;
import io.piagents.events.v2.FlowEvent;
import org.apache.flink.api.common.serialization.AbstractDeserializationSchema;

/**
 * Decodes {@code traffic.flows.v2} values. Bytes that are not a FlowEvent, or
 * lack the identity the job keys and deduplicates on, become malformed records
 * instead of failing the job.
 */
public class FlowEventDeserializer extends AbstractDeserializationSchema<FlowRecord> {

  @Override
  public FlowRecord deserialize(byte[] message) {
    if (message == null) {
      return FlowRecord.malformed("empty value", null);
    }
    FlowEvent event;
    try {
      event = FlowEvent.parseFrom(message);
    } catch (InvalidProtocolBufferException e) {
      return FlowRecord.malformed("not a FlowEvent: " + e.getMessage(), message);
    }
    if (!event.hasEnvelope()
        || event.getEnvelope().getEventId().isEmpty()
        || event.getEnvelope().getDeviceKey().isEmpty()
        || event.getEnvelope().getEventTimeUnixMillis() <= 0) {
      return FlowRecord.malformed("missing event id, device key or event time", message);
    }
    return FlowRecord.fromEvent(event, message);
  }
}
