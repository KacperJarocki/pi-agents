//! librdkafka-backed `Publisher` (feature `kafka`): idempotent producer with
//! `acks=all`. Messages never time out inside librdkafka, so during a broker
//! outage they wait in its bounded queue and the rest stay in the spool;
//! `send` reports `Full` when that queue is at its limit.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use rdkafka::config::ClientConfig;
use rdkafka::error::{KafkaError, KafkaResult, RDKafkaErrorCode};
use rdkafka::producer::{BaseProducer, BaseRecord, DeliveryResult, Producer, ProducerContext};
use rdkafka::ClientContext;

use crate::forward::{Publisher, SendOutcome};
use crate::spool::Entry;

type Reports = Arc<Mutex<Vec<(u64, bool)>>>;

/// Collects delivery reports for `KafkaPublisher::poll`.
pub struct DeliveryContext {
    reports: Reports,
}

impl ClientContext for DeliveryContext {}

impl ProducerContext for DeliveryContext {
    type DeliveryOpaque = usize;

    fn delivery(&self, result: &DeliveryResult<'_>, seq: usize) {
        if let Err((err, _)) = result {
            eprintln!("warning: delivery of sequence {seq} failed: {err}");
        }
        self.reports
            .lock()
            .expect("delivery report lock")
            .push((seq as u64, result.is_ok()));
    }
}

pub struct KafkaPublisher {
    producer: BaseProducer<DeliveryContext>,
    reports: Reports,
}

impl KafkaPublisher {
    pub fn new(bootstrap_servers: &str) -> KafkaResult<Self> {
        let reports = Reports::default();
        let producer = ClientConfig::new()
            .set("bootstrap.servers", bootstrap_servers)
            .set("client.id", "pi-agents-sensor")
            .set("acks", "all")
            .set("enable.idempotence", "true")
            .set("linger.ms", "20")
            .set("compression.type", "lz4")
            // Keep retrying through outages; the spool bounds total backlog.
            .set("message.timeout.ms", "0")
            // Bound librdkafka's own memory inside the 0.5 GiB pod.
            .set("queue.buffering.max.kbytes", "65536")
            .set("queue.buffering.max.messages", "200000")
            .create_with_context(DeliveryContext {
                reports: reports.clone(),
            })?;
        Ok(Self { producer, reports })
    }

    /// Waits up to `timeout` for queued messages to be delivered.
    pub fn flush(&mut self, timeout: Duration) {
        let _ = self.producer.flush(timeout);
    }
}

impl Publisher for KafkaPublisher {
    fn send(&mut self, topic: &str, entry: &Entry) -> SendOutcome {
        let record = BaseRecord::with_opaque_to(topic, entry.seq as usize)
            .key(entry.key.as_slice())
            .payload(entry.payload.as_slice());
        match self.producer.send(record) {
            Ok(()) => SendOutcome::Queued,
            Err((KafkaError::MessageProduction(RDKafkaErrorCode::QueueFull), _)) => {
                SendOutcome::Full
            }
            Err((err, _)) => {
                // Anything else (e.g. unknown topic before metadata arrives)
                // is retried from the spool on the next pump.
                eprintln!("warning: cannot enqueue sequence {}: {err}", entry.seq);
                SendOutcome::Full
            }
        }
    }

    fn poll(&mut self) -> Vec<(u64, bool)> {
        self.producer.poll(Duration::ZERO);
        std::mem::take(&mut *self.reports.lock().expect("delivery report lock"))
    }
}
