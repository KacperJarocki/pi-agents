//! Hand-written Protobuf encoder for the `pi_agents.events.v2` messages the
//! sensor produces (`FlowEvent`, `HealthEvent` and their `Envelope`). Field
//! numbers follow `schemas/events/v2/events.proto`; a test checks them against
//! `compatibility.json`. Default values (0, "", false) are omitted, as proto3
//! encoders do.

use crate::packet::{format_mac, Protocol};
use crate::{AttemptOutcome, Direction, FlowRecord};

pub const SCHEMA_VERSION: &str = "v2";

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Envelope {
    pub event_id: String,
    pub gateway_id: String,
    pub device_key: String,
    pub event_time_unix_millis: i64,
    pub ingest_time_unix_millis: i64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HealthKind {
    Heartbeat = 1,
    SpoolLoss = 2,
    Unattributed = 3,
    FlowTableEviction = 5,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HealthEvent {
    pub envelope: Envelope,
    pub component: String,
    pub kind: HealthKind,
    pub count: u64,
    pub first_sequence: u64,
    pub last_sequence: u64,
    pub first_event_time_unix_millis: i64,
    pub last_event_time_unix_millis: i64,
    pub detail: String,
}

#[derive(Default)]
struct Writer {
    buf: Vec<u8>,
}

impl Writer {
    fn varint(&mut self, mut v: u64) {
        while v >= 0x80 {
            self.buf.push((v as u8) | 0x80);
            v >>= 7;
        }
        self.buf.push(v as u8);
    }

    fn tag(&mut self, field: u32, wire_type: u8) {
        self.varint(u64::from(field) << 3 | u64::from(wire_type));
    }

    fn uint(&mut self, field: u32, v: u64) {
        if v != 0 {
            self.tag(field, 0);
            self.varint(v);
        }
    }

    /// int64 uses plain two's-complement varints (not zigzag).
    fn int(&mut self, field: u32, v: i64) {
        self.uint(field, v as u64);
    }

    fn boolean(&mut self, field: u32, v: bool) {
        self.uint(field, u64::from(v));
    }

    fn bytes(&mut self, field: u32, v: &[u8]) {
        if !v.is_empty() {
            self.tag(field, 2);
            self.varint(v.len() as u64);
            self.buf.extend_from_slice(v);
        }
    }

    fn string(&mut self, field: u32, v: &str) {
        self.bytes(field, v.as_bytes());
    }

    fn message(&mut self, field: u32, v: &[u8]) {
        // Embedded messages are written even when empty.
        self.tag(field, 2);
        self.varint(v.len() as u64);
        self.buf.extend_from_slice(v);
    }
}

fn encode_envelope(e: &Envelope) -> Vec<u8> {
    let mut w = Writer::default();
    w.string(1, &e.event_id);
    w.string(2, SCHEMA_VERSION);
    w.string(3, &e.gateway_id);
    w.string(4, &e.device_key);
    w.int(5, e.event_time_unix_millis);
    w.int(6, e.ingest_time_unix_millis);
    w.buf
}

fn protocol_number(p: Protocol) -> u64 {
    match p {
        Protocol::Tcp => 1,
        Protocol::Udp => 2,
        Protocol::Icmp => 3,
        Protocol::Icmpv6 => 4,
        Protocol::Other(_) => 15,
    }
}

fn direction_number(d: Direction) -> u64 {
    match d {
        Direction::Outbound => 1,
        Direction::Inbound => 2,
    }
}

fn outcome_number(o: AttemptOutcome) -> u64 {
    match o {
        AttemptOutcome::NotApplicable => 1,
        AttemptOutcome::Established => 2,
        AttemptOutcome::Rejected => 3,
        AttemptOutcome::Unanswered => 4,
    }
}

pub fn device_key(gateway_id: &str, record: &FlowRecord) -> String {
    format!("{gateway_id}:{}", format_mac(&record.key.device_mac))
}

pub fn flow_event_id(gateway_id: &str, capture_sequence: u64) -> String {
    format!("{gateway_id}/{capture_sequence}")
}

/// Encodes a flushed micro-flow as a `FlowEvent`. The envelope's event time
/// is the last packet's time.
pub fn encode_flow_event(
    gateway_id: &str,
    capture_sequence: u64,
    ingest_time_unix_millis: i64,
    record: &FlowRecord,
) -> Vec<u8> {
    let f = &record.flow;
    let k = &record.key;
    let envelope = Envelope {
        event_id: flow_event_id(gateway_id, capture_sequence),
        gateway_id: gateway_id.to_string(),
        device_key: device_key(gateway_id, record),
        event_time_unix_millis: f.last_packet_us.div_euclid(1000),
        ingest_time_unix_millis,
    };
    let (src_ip, dst_ip, src_port, dst_port) = match f.direction {
        Direction::Outbound => (k.device_ip, k.remote_ip, k.device_port, k.remote_port),
        Direction::Inbound => (k.remote_ip, k.device_ip, k.remote_port, k.device_port),
    };
    let mut w = Writer::default();
    w.message(1, &encode_envelope(&envelope));
    w.uint(2, capture_sequence);
    w.string(3, &format_mac(&k.device_mac));
    w.string(4, &src_ip.to_string());
    w.string(5, &dst_ip.to_string());
    w.uint(6, u64::from(src_port));
    w.uint(7, u64::from(dst_port));
    w.uint(8, protocol_number(k.protocol));
    w.uint(9, direction_number(f.direction));
    w.int(10, f.first_packet_us);
    w.int(11, f.last_packet_us);
    w.uint(12, u64::from(f.packets_from_device));
    w.uint(13, u64::from(f.packets_to_device));
    w.uint(14, f.bytes_from_device);
    w.uint(15, f.bytes_to_device);
    w.uint(16, u64::from(f.tcp_syn));
    w.uint(17, u64::from(f.tcp_syn_ack));
    w.uint(18, u64::from(f.tcp_rst));
    w.uint(19, u64::from(f.tcp_fin));
    w.uint(20, outcome_number(record.attempt_outcome));
    w.string(21, f.dns_query.as_deref().unwrap_or(""));
    w.boolean(22, record.continuation);
    w.buf
}

pub fn encode_health_event(h: &HealthEvent) -> Vec<u8> {
    let mut w = Writer::default();
    w.message(1, &encode_envelope(&h.envelope));
    w.string(2, &h.component);
    w.uint(3, h.kind as u64);
    w.uint(4, h.count);
    w.uint(5, h.first_sequence);
    w.uint(6, h.last_sequence);
    w.int(7, h.first_event_time_unix_millis);
    w.int(8, h.last_event_time_unix_millis);
    w.string(9, &h.detail);
    w.buf
}

/// Minimal wire-format decoder for tests: returns (field, wire type, value).
#[cfg(test)]
pub(crate) fn decode_fields(mut b: &[u8]) -> Vec<(u32, u8, Vec<u8>)> {
    fn varint(b: &mut &[u8]) -> u64 {
        let mut v = 0u64;
        let mut shift = 0;
        loop {
            let byte = b[0];
            *b = &b[1..];
            v |= u64::from(byte & 0x7f) << shift;
            if byte < 0x80 {
                return v;
            }
            shift += 7;
        }
    }
    let mut fields = Vec::new();
    while !b.is_empty() {
        let key = varint(&mut b);
        let (field, wire_type) = ((key >> 3) as u32, (key & 7) as u8);
        let value = match wire_type {
            0 => varint(&mut b).to_le_bytes().to_vec(),
            2 => {
                let len = varint(&mut b) as usize;
                let (v, rest) = b.split_at(len);
                b = rest;
                v.to_vec()
            }
            other => panic!("unexpected wire type {other}"),
        };
        fields.push((field, wire_type, value));
    }
    fields
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{FlowKey, FlowTable, Observation, TCP_SYN};
    use std::collections::BTreeSet;
    use std::net::{IpAddr, Ipv4Addr};

    const COMPATIBILITY: &str = include_str!("../../../schemas/events/v2/compatibility.json");

    /// Field numbers declared for `message` in compatibility.json.
    fn declared(message: &str) -> BTreeSet<u32> {
        let start = COMPATIBILITY
            .find(&format!("\"{message}\": {{"))
            .expect("message in compatibility.json");
        let body = &COMPATIBILITY[start..];
        let body = &body[body.find('{').unwrap() + 1..body.find('}').unwrap()];
        body.split(',')
            .filter_map(|pair| pair.split(':').nth(1))
            .map(|n| n.trim().parse().expect("field number"))
            .collect()
    }

    fn record() -> FlowRecord {
        let mut table = FlowTable::new(4);
        let mut out = Vec::new();
        let key = FlowKey {
            device_mac: [0x02, 0, 0, 0, 0, 0x0a],
            device_ip: IpAddr::V4(Ipv4Addr::new(192, 168, 50, 10)),
            remote_ip: IpAddr::V4(Ipv4Addr::new(203, 0, 113, 10)),
            device_port: 40000,
            remote_port: 22,
            protocol: Protocol::Tcp,
        };
        table.upsert(
            Observation {
                key,
                timestamp_us: 1_760_000_000_123_456,
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

    #[test]
    fn flow_event_uses_only_declared_field_numbers() {
        let bytes = encode_flow_event("gw-1", 42, 1_760_000_000_200, &record());
        let fields = decode_fields(&bytes);
        let used: BTreeSet<u32> = fields.iter().map(|f| f.0).collect();
        assert!(used.is_subset(&declared("FlowEvent")), "{used:?}");

        let envelope = &fields.iter().find(|f| f.0 == 1).expect("envelope").2;
        let env_used: BTreeSet<u32> = decode_fields(envelope).iter().map(|f| f.0).collect();
        assert!(env_used.is_subset(&declared("Envelope")), "{env_used:?}");
    }

    #[test]
    fn flow_event_carries_identity_times_and_outcome() {
        let bytes = encode_flow_event("gw-1", 42, 1_760_000_000_200, &record());
        let fields = decode_fields(&bytes);
        let get = |n: u32| fields.iter().find(|f| f.0 == n).map(|f| f.2.clone());
        let as_u64 = |v: Vec<u8>| u64::from_le_bytes(v.try_into().unwrap());

        assert_eq!(as_u64(get(2).unwrap()), 42);
        assert_eq!(get(3).unwrap(), b"02:00:00:00:00:0a");
        assert_eq!(get(4).unwrap(), b"192.168.50.10");
        assert_eq!(as_u64(get(7).unwrap()), 22);
        assert_eq!(as_u64(get(10).unwrap()), 1_760_000_000_123_456);
        assert_eq!(as_u64(get(20).unwrap()), 4); // unanswered
        assert_eq!(get(22), None); // false is omitted

        let env = decode_fields(&get(1).unwrap());
        let env_get = |n: u32| env.iter().find(|f| f.0 == n).map(|f| f.2.clone());
        assert_eq!(env_get(1).unwrap(), b"gw-1/42");
        assert_eq!(env_get(2).unwrap(), b"v2");
        assert_eq!(env_get(4).unwrap(), b"gw-1:02:00:00:00:00:0a");
        assert_eq!(as_u64(env_get(5).unwrap()), 1_760_000_000_123);
        assert_eq!(as_u64(env_get(6).unwrap()), 1_760_000_000_200);
    }

    #[test]
    fn health_event_uses_only_declared_field_numbers() {
        let event = HealthEvent {
            envelope: Envelope {
                event_id: "gw-1/7".into(),
                gateway_id: "gw-1".into(),
                ..Envelope::default()
            },
            component: "sensor-spool".into(),
            kind: HealthKind::SpoolLoss,
            count: 3,
            first_sequence: 10,
            last_sequence: 12,
            first_event_time_unix_millis: 1,
            last_event_time_unix_millis: 2,
            detail: "spool full".into(),
        };
        let used: BTreeSet<u32> = decode_fields(&encode_health_event(&event))
            .iter()
            .map(|f| f.0)
            .collect();
        assert_eq!(used, declared("HealthEvent"));
    }

    #[test]
    fn varints_round_trip_large_and_negative_values() {
        let mut w = Writer::default();
        w.int(1, -1);
        w.uint(2, u64::MAX);
        let fields = decode_fields(&w.buf);
        assert_eq!(
            u64::from_le_bytes(fields[0].2.clone().try_into().unwrap()),
            u64::MAX
        );
        assert_eq!(fields[1].0, 2);
    }
}
