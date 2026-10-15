package io.piagents.detection;

import java.nio.charset.StandardCharsets;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.header.internals.RecordHeaders;

/**
 * Writes late or malformed events to {@code traffic.late.v2}: key = device key,
 * value = the original bytes, headers = reason and detail.
 */
public class LateEventSerializer implements KafkaRecordSerializationSchema<LateEvent> {
  private final String topic;

  public LateEventSerializer(String topic) {
    this.topic = topic;
  }

  @Override
  public ProducerRecord<byte[], byte[]> serialize(
      LateEvent event, KafkaSinkContext context, Long timestamp) {
    RecordHeaders headers = new RecordHeaders();
    headers.add("pi-agents-reason", event.reason.getBytes(StandardCharsets.UTF_8));
    headers.add("pi-agents-detail", event.detail.getBytes(StandardCharsets.UTF_8));
    byte[] key = event.deviceKey.isEmpty() ? null : event.deviceKey.getBytes(StandardCharsets.UTF_8);
    return new ProducerRecord<>(topic, null, timestamp, key, event.raw, headers);
  }
}
