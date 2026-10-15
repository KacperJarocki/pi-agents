package io.piagents.detection;

import io.piagents.events.v2.DetectionTransition;
import io.piagents.events.v2.Envelope;
import io.piagents.events.v2.HorizonEvidence;
import io.piagents.events.v2.Severity;
import io.piagents.events.v2.ThreatFamily;
import io.piagents.events.v2.TransitionStatus;
import java.nio.charset.StandardCharsets;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.kafka.clients.producer.ProducerRecord;

/** Writes transitions to {@code traffic.detections.v2}, keyed by incident ID. */
public class TransitionSerializer implements KafkaRecordSerializationSchema<Transition> {
  public static final String FEATURE_VERSION = "rolling-views/v1";

  private final String topic;

  public TransitionSerializer(String topic) {
    this.topic = topic;
  }

  public static DetectionTransition toProto(Transition t) {
    int colon = t.deviceKey.indexOf(':');
    String gatewayId = colon > 0 ? t.deviceKey.substring(0, colon) : "";
    DetectionTransition.Builder b =
        DetectionTransition.newBuilder()
            .setEnvelope(
                Envelope.newBuilder()
                    .setEventId(t.transitionId())
                    .setSchemaVersion("v2")
                    .setGatewayId(gatewayId)
                    .setDeviceKey(t.deviceKey)
                    .setEventTimeUnixMillis(t.evidenceEventTimeMs)
                    .setIngestTimeUnixMillis(t.detectedAtMs))
            .setTransitionId(t.transitionId())
            .setIncidentId(t.incidentId)
            .setRevision(t.revision)
            .setStatus(TransitionStatus.forNumber(t.status))
            .setThreatFamily(ThreatFamily.THREAT_FAMILY_PORT_SCAN)
            .setRuleId(t.ruleId)
            .setRuleVersion(t.ruleVersion)
            .setSeverity(Severity.forNumber(t.severity))
            .setEvidenceEventTimeUnixMillis(t.evidenceEventTimeMs)
            .setDetectedAtUnixMillis(t.detectedAtMs)
            .setEpisodeStartUnixMillis(t.episodeStartMs)
            .setFeatureVersion(FEATURE_VERSION)
            .setEvidenceIngestTimeUnixMillis(t.evidenceIngestTimeMs);
    for (int h = 0; h < t.horizonSeconds.length; h++) {
      b.addHorizons(
          HorizonEvidence.newBuilder()
              .setHorizonSeconds(t.horizonSeconds[h])
              .setValue(t.values[h])
              .setOpenThreshold(t.openThresholds[h])
              .setOverThreshold(t.overThreshold[h]));
    }
    for (String id : t.sampleEventIds) {
      b.addSampleEventIds(id);
    }
    return b.build();
  }

  @Override
  public ProducerRecord<byte[], byte[]> serialize(
      Transition t, KafkaSinkContext context, Long timestamp) {
    return new ProducerRecord<>(
        topic,
        null,
        timestamp,
        t.incidentId.getBytes(StandardCharsets.UTF_8),
        toProto(t).toByteArray());
  }
}
