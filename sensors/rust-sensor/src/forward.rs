//! Moves events from the sensor through the spool to a `Publisher`.
//!
//! Every flushed micro-flow is encoded as a v2 `FlowEvent`, appended to the
//! spool (which assigns its `capture_sequence`) and later sent in sequence
//! order. Delivery reports acknowledge spool entries; a failed delivery
//! rewinds sending to the oldest unacknowledged entry, so nothing is lost and
//! duplicates are possible (consumers deduplicate by event ID). Spool loss
//! and sensor counters are published as `HealthEvent`s.

use std::io;

use crate::proto::{
    device_key, encode_flow_event, encode_health_event, Envelope, HealthEvent, HealthKind,
};
use crate::spool::{Cursor, Entry, Loss, Spool, Topic};
use crate::FlowRecord;

pub const FLOWS_TOPIC: &str = "traffic.flows.v2";
pub const HEALTH_TOPIC: &str = "traffic.health.v2";

#[derive(Debug, Eq, PartialEq)]
pub enum SendOutcome {
    /// Handed to the client; a delivery report follows.
    Queued,
    /// The client cannot take more now; retry later from the same entry.
    Full,
}

pub trait Publisher {
    fn send(&mut self, topic: &str, entry: &Entry) -> SendOutcome;
    /// Delivery reports since the last call: (sequence, delivered).
    fn poll(&mut self) -> Vec<(u64, bool)>;
}

pub fn topic_name(topic: Topic) -> &'static str {
    match topic {
        Topic::Flows => FLOWS_TOPIC,
        Topic::Health => HEALTH_TOPIC,
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ForwardStats {
    pub sent: u64,
    pub delivered: u64,
    pub failed_deliveries: u64,
    pub lost: u64,
}

pub struct Forwarder<P> {
    gateway_id: String,
    spool: Spool,
    publisher: P,
    cursor: Cursor,
    stats: ForwardStats,
    /// All spool loss since start; every loss report carries the running
    /// total so a later report survives even if earlier ones were dropped.
    cumulative_loss: Option<Loss>,
}

impl<P: Publisher> Forwarder<P> {
    pub fn new(gateway_id: impl Into<String>, spool: Spool, publisher: P) -> Self {
        let cursor = spool.rewind();
        Self {
            gateway_id: gateway_id.into(),
            spool,
            publisher,
            cursor,
            stats: ForwardStats::default(),
            cumulative_loss: None,
        }
    }

    pub fn stats(&self) -> &ForwardStats {
        &self.stats
    }

    pub fn spool(&self) -> &Spool {
        &self.spool
    }

    pub fn publisher_mut(&mut self) -> &mut P {
        &mut self.publisher
    }

    /// Appends a flushed micro-flow. `now_ms` becomes the envelope's ingest
    /// time and is preserved when the event is re-sent.
    pub fn submit_flow(&mut self, record: &FlowRecord, now_ms: i64) -> io::Result<u64> {
        let gateway_id = self.gateway_id.clone();
        let key = device_key(&gateway_id, record);
        let event_time_ms = record.flow.last_packet_us.div_euclid(1000);
        let (seq, loss) =
            self.spool
                .append(Topic::Flows, key.as_bytes(), event_time_ms, |seq| {
                    encode_flow_event(&gateway_id, seq, now_ms, record)
                })?;
        if let Some(loss) = loss {
            self.report_loss(&loss, now_ms)?;
        }
        Ok(seq)
    }

    /// Appends a health event; its ID is derived from gateway, kind and
    /// sequence so a re-send keeps the same ID.
    #[allow(clippy::too_many_arguments)]
    pub fn submit_health(
        &mut self,
        component: &str,
        kind: HealthKind,
        count: u64,
        sequences: (u64, u64),
        event_times_ms: (i64, i64),
        detail: &str,
        now_ms: i64,
    ) -> io::Result<u64> {
        let gateway_id = self.gateway_id.clone();
        let (seq, loss) = self.spool.append(
            Topic::Health,
            gateway_id.as_bytes(),
            event_times_ms.1,
            |seq| {
                encode_health_event(&HealthEvent {
                    envelope: Envelope {
                        event_id: format!("{gateway_id}/{seq}"),
                        gateway_id: gateway_id.clone(),
                        device_key: String::new(),
                        event_time_unix_millis: event_times_ms.1,
                        ingest_time_unix_millis: now_ms,
                    },
                    component: component.to_string(),
                    kind,
                    count,
                    first_sequence: sequences.0,
                    last_sequence: sequences.1,
                    first_event_time_unix_millis: event_times_ms.0,
                    last_event_time_unix_millis: event_times_ms.1,
                    detail: detail.to_string(),
                })
            },
        )?;
        if let Some(loss) = loss {
            self.report_loss(&loss, now_ms)?;
        }
        Ok(seq)
    }

    fn report_loss(&mut self, loss: &Loss, now_ms: i64) -> io::Result<()> {
        self.stats.lost += loss.count;
        let total = match self.cumulative_loss.take() {
            None => loss.clone(),
            Some(t) => Loss {
                count: t.count + loss.count,
                first_seq: t.first_seq.min(loss.first_seq),
                last_seq: t.last_seq.max(loss.last_seq),
                first_event_time_ms: t.first_event_time_ms.min(loss.first_event_time_ms),
                last_event_time_ms: t.last_event_time_ms.max(loss.last_event_time_ms),
            },
        };
        self.cumulative_loss = Some(total.clone());
        let loss = &total;
        // A loss report can itself push the spool over its limit only if a
        // segment is smaller than one record; the nested report is then dropped.
        let gateway_id = self.gateway_id.clone();
        let detail = "spool full: oldest unacknowledged events dropped (totals since sensor start)";
        let (_, nested) = self.spool.append(
            Topic::Health,
            gateway_id.as_bytes(),
            loss.last_event_time_ms,
            |seq| {
                encode_health_event(&HealthEvent {
                    envelope: Envelope {
                        event_id: format!("{gateway_id}/{seq}"),
                        gateway_id: gateway_id.clone(),
                        device_key: String::new(),
                        event_time_unix_millis: loss.last_event_time_ms,
                        ingest_time_unix_millis: now_ms,
                    },
                    component: "sensor-spool".to_string(),
                    kind: HealthKind::SpoolLoss,
                    count: loss.count,
                    first_sequence: loss.first_seq,
                    last_sequence: loss.last_seq,
                    first_event_time_unix_millis: loss.first_event_time_ms,
                    last_event_time_unix_millis: loss.last_event_time_ms,
                    detail: detail.to_string(),
                })
            },
        )?;
        if let Some(nested) = nested {
            self.stats.lost += nested.count;
            if let Some(total) = self.cumulative_loss.as_mut() {
                total.count += nested.count;
                total.last_seq = total.last_seq.max(nested.last_seq);
            }
        }
        Ok(())
    }

    /// Processes delivery reports, then sends up to `budget` spooled entries.
    pub fn pump(&mut self, budget: usize) -> io::Result<()> {
        let mut failed = false;
        for (seq, delivered) in self.publisher.poll() {
            if delivered {
                self.stats.delivered += 1;
                self.spool.ack(seq)?;
            } else {
                self.stats.failed_deliveries += 1;
                failed = true;
            }
        }
        if failed {
            self.cursor = self.spool.rewind();
        }

        let mut remaining = budget;
        while remaining > 0 {
            let mut cursor = self.cursor;
            let batch = self.spool.read(&mut cursor, remaining.min(256))?;
            if batch.is_empty() {
                self.cursor = cursor;
                return Ok(());
            }
            for (position, entry) in &batch {
                match self.publisher.send(topic_name(entry.topic), entry) {
                    SendOutcome::Queued => {
                        self.stats.sent += 1;
                        remaining -= 1;
                    }
                    SendOutcome::Full => {
                        self.cursor = *position;
                        return Ok(());
                    }
                }
            }
            self.cursor = cursor;
        }
        Ok(())
    }

    pub fn sync(&mut self) -> io::Result<()> {
        self.spool.sync()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::decode_fields;
    use crate::spool::SpoolConfig;
    use crate::{FlowKey, FlowTable, Observation, TCP_SYN};
    use std::collections::VecDeque;
    use std::net::{IpAddr, Ipv4Addr};
    use std::path::PathBuf;

    /// In-memory broker: while `up` it delivers queued messages in order on
    /// the next poll; while down it holds up to `queue_limit` messages and
    /// then fails them all, like librdkafka timing out.
    struct FakeBroker {
        up: bool,
        queue_limit: usize,
        queue: VecDeque<(String, Entry)>,
        log: Vec<(String, Entry)>,
    }

    impl FakeBroker {
        fn new(queue_limit: usize) -> Self {
            Self {
                up: true,
                queue_limit,
                queue: VecDeque::new(),
                log: Vec::new(),
            }
        }

        fn delivered_seqs(&self, topic: &str) -> Vec<u64> {
            self.log
                .iter()
                .filter(|(t, _)| t == topic)
                .map(|(_, e)| e.seq)
                .collect()
        }
    }

    impl Publisher for FakeBroker {
        fn send(&mut self, topic: &str, entry: &Entry) -> SendOutcome {
            if self.queue.len() >= self.queue_limit {
                return SendOutcome::Full;
            }
            self.queue.push_back((topic.to_string(), entry.clone()));
            SendOutcome::Queued
        }

        fn poll(&mut self) -> Vec<(u64, bool)> {
            if self.up {
                let reports = self.queue.iter().map(|(_, e)| (e.seq, true)).collect();
                self.log.extend(self.queue.drain(..));
                reports
            } else if self.queue.len() >= self.queue_limit {
                self.queue.drain(..).map(|(_, e)| (e.seq, false)).collect()
            } else {
                Vec::new()
            }
        }
    }

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(name: &str) -> Self {
            let dir = std::env::temp_dir().join(format!(
                "pi-agents-forward-{name}-{}-{:?}",
                std::process::id(),
                std::time::SystemTime::now()
            ));
            std::fs::create_dir_all(&dir).unwrap();
            Self(dir)
        }

        fn spool(&self, segment_bytes: u64, max_bytes: u64) -> Spool {
            Spool::open(SpoolConfig {
                dir: self.0.clone(),
                segment_bytes,
                max_bytes,
            })
            .unwrap()
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn record(port: u16, t_us: i64) -> FlowRecord {
        let mut table = FlowTable::new(4);
        let mut out = Vec::new();
        table.upsert(
            Observation {
                key: FlowKey {
                    device_mac: [0x02, 0, 0, 0, 0, 0x0a],
                    device_ip: IpAddr::V4(Ipv4Addr::new(192, 168, 50, 10)),
                    remote_ip: IpAddr::V4(Ipv4Addr::new(203, 0, 113, 10)),
                    device_port: 40000,
                    remote_port: port,
                    protocol: crate::packet::Protocol::Tcp,
                },
                timestamp_us: t_us,
                from_device: true,
                bytes: 54,
                tcp_flags: TCP_SYN,
                rejection: false,
                dns_query: None,
            },
            &mut out,
        );
        table.flush_all(&mut out);
        out.remove(0)
    }

    fn event_id(payload: &[u8]) -> String {
        let envelope = decode_fields(payload)
            .into_iter()
            .find(|f| f.0 == 1)
            .expect("envelope")
            .2;
        let id = decode_fields(&envelope)
            .into_iter()
            .find(|f| f.0 == 1)
            .expect("event_id")
            .2;
        String::from_utf8(id).unwrap()
    }

    #[test]
    fn broker_outage_and_recovery_deliver_everything_in_order() {
        let dir = TempDir::new("outage");
        let mut fwd = Forwarder::new("gw-1", dir.spool(4096, 1 << 20), FakeBroker::new(8));
        for i in 0..5 {
            fwd.submit_flow(&record(1000 + i, 1_000_000 * i64::from(i)), 1)
                .unwrap();
        }
        fwd.pump(100).unwrap();
        fwd.pump(100).unwrap();

        fwd.publisher_mut().up = false;
        for i in 5..40 {
            fwd.submit_flow(&record(1000 + i, 1_000_000 * i64::from(i)), 2)
                .unwrap();
            fwd.pump(100).unwrap();
        }
        assert!(
            fwd.stats().failed_deliveries > 0,
            "outage must surface as failures"
        );
        assert_eq!(fwd.spool().unacked(), 35);

        fwd.publisher_mut().up = true;
        for _ in 0..20 {
            fwd.pump(100).unwrap();
        }
        assert_eq!(fwd.spool().unacked(), 0);

        let delivered = fwd.publisher_mut().delivered_seqs(FLOWS_TOPIC);
        let mut first_seen = Vec::new();
        for seq in &delivered {
            if !first_seen.contains(seq) {
                first_seen.push(*seq);
            }
        }
        assert_eq!(first_seen, (0..40).collect::<Vec<_>>(), "ordered, no loss");
        assert_eq!(fwd.stats().lost, 0);

        let first = &fwd.publisher_mut().log[0].1;
        assert_eq!(event_id(&first.payload), "gw-1/0");
        assert_eq!(first.key, b"gw-1:02:00:00:00:00:0a");
    }

    #[test]
    fn restart_resends_undelivered_events_without_reusing_ids() {
        let dir = TempDir::new("restart");
        {
            let mut broker = FakeBroker::new(100);
            broker.up = false;
            let mut fwd = Forwarder::new("gw-1", dir.spool(4096, 1 << 20), broker);
            for i in 0..3 {
                fwd.submit_flow(&record(2000 + i, 0), 1).unwrap();
            }
            fwd.pump(100).unwrap();
            fwd.sync().unwrap();
        }
        let mut fwd = Forwarder::new("gw-1", dir.spool(4096, 1 << 20), FakeBroker::new(100));
        let new_seq = fwd.submit_flow(&record(3000, 0), 1).unwrap();
        fwd.pump(100).unwrap();
        fwd.pump(100).unwrap();

        let ids: Vec<String> = fwd
            .publisher_mut()
            .log
            .iter()
            .map(|(_, e)| event_id(&e.payload))
            .collect();
        assert_eq!(&ids[..3], ["gw-1/0", "gw-1/1", "gw-1/2"]);
        assert!(new_seq > 2, "sequence reused after restart");
        assert_eq!(ids[3], format!("gw-1/{new_seq}"));
        assert_eq!(fwd.spool().unacked(), 0);
    }

    #[test]
    fn spool_exhaustion_emits_a_loss_health_event() {
        let dir = TempDir::new("exhaust");
        let mut broker = FakeBroker::new(1_000_000);
        broker.up = false;
        let mut fwd = Forwarder::new("gw-1", dir.spool(2048, 4096), broker);
        for i in 0..200 {
            fwd.submit_flow(&record(4000 + (i % 1000) as u16, 5_000 * i64::from(i)), 9)
                .unwrap();
        }
        let lost = fwd.stats().lost;
        assert!(lost > 0);
        drop(fwd);

        // A restarted sensor drains what is left; the health topic carries
        // the cumulative loss report.
        let mut fresh = Forwarder::new("gw-1", dir.spool(2048, 4096), FakeBroker::new(1_000_000));
        for _ in 0..10 {
            fresh.pump(1000).unwrap();
        }
        let health: Vec<&Entry> = fresh
            .publisher_mut()
            .log
            .iter()
            .filter(|(t, _)| t == HEALTH_TOPIC)
            .map(|(_, e)| e)
            .collect();
        assert!(!health.is_empty(), "loss must be reported");
        let fields = decode_fields(&health.last().unwrap().payload);
        let get = |n: u32| {
            fields
                .iter()
                .find(|f| f.0 == n)
                .map(|f| u64::from_le_bytes(f.2.clone().try_into().unwrap()))
        };
        assert_eq!(get(3), Some(HealthKind::SpoolLoss as u64));
        assert!(get(4).unwrap() > 0 && get(4).unwrap() <= lost);
        // proto3 omits zero: a missing first_sequence means sequence 0.
        assert_eq!(get(5).unwrap_or(0), 0, "first lost sequence");
        assert!(get(6).unwrap() > 0);
    }
}
