package io.piagents.detection;

import io.piagents.events.v2.Envelope;
import io.piagents.events.v2.FeatureUpdate;
import java.nio.charset.StandardCharsets;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.kafka.clients.producer.ProducerRecord;

/** Writes views to {@code traffic.features.v2} as FeatureUpdate, keyed by device key. */
public class FeatureUpdateSerializer implements KafkaRecordSerializationSchema<DeviceWindows.View> {
  private final String topic;

  public FeatureUpdateSerializer(String topic) {
    this.topic = topic;
  }

  public static FeatureUpdate toProto(DeviceWindows.View v) {
    int colon = v.deviceKey.indexOf(':');
    return FeatureUpdate.newBuilder()
        .setEnvelope(
            Envelope.newBuilder()
                .setEventId(v.deviceKey + "/" + v.horizonSeconds + "/" + v.windowEndMs)
                .setSchemaVersion("v2")
                .setGatewayId(colon > 0 ? v.deviceKey.substring(0, colon) : "")
                .setDeviceKey(v.deviceKey)
                .setEventTimeUnixMillis(v.windowEndMs)
                .setIngestTimeUnixMillis(System.currentTimeMillis()))
        .setHorizonSeconds(v.horizonSeconds)
        .setWindowEndUnixMillis(v.windowEndMs)
        .setFeatureVersion(TransitionSerializer.FEATURE_VERSION)
        .putFeatures("flows", v.flows)
        .putFeatures("bytes", v.bytes)
        .putFeatures("distinct_remote_ips", v.distinctRemoteIps)
        .putFeatures("distinct_targets", v.distinctTargets)
        .putFeatures("failed_targets", v.failedTargets)
        .build();
  }

  @Override
  public ProducerRecord<byte[], byte[]> serialize(
      DeviceWindows.View v, KafkaSinkContext context, Long timestamp) {
    return new ProducerRecord<>(
        topic, null, timestamp, v.deviceKey.getBytes(StandardCharsets.UTF_8), toProto(v).toByteArray());
  }
}
