//! Gateway sensor core: turns captured Ethernet frames into device-attributed
//! micro-flows.
//!
//! A micro-flow aggregates both directions of one 5-tuple, oriented from the
//! device's point of view, and is flushed after 1 s of inactivity or 1 s after
//! its first packet, whichever comes first, so aggregation adds at most 1 s of
//! latency. The core is dependency-free; live capture (libpcap) lives behind
//! the `live` feature and Kafka publishing in a later component.

pub mod attribution;
pub mod packet;
pub mod pcapfile;

use std::collections::HashMap;
use std::net::IpAddr;

use attribution::{Attribution, Clients};
use packet::{
    format_mac, parse_ethernet, Icmp, Mac, ParseError, Protocol, TCP_ACK, TCP_FIN, TCP_RST, TCP_SYN,
};

pub const DEFAULT_IDLE_US: i64 = 1_000_000;
pub const DEFAULT_ACTIVE_US: i64 = 1_000_000;
/// How long a flushed 5-tuple is remembered to mark follow-up events as
/// continuations and keep their direction.
pub const CONTINUATION_TTL_US: i64 = 120_000_000;

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct FlowKey {
    pub device_mac: Mac,
    pub device_ip: IpAddr,
    pub remote_ip: IpAddr,
    pub device_port: u16,
    pub remote_port: u16,
    pub protocol: Protocol,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Direction {
    /// The device initiated the micro-flow.
    Outbound,
    /// A remote endpoint initiated it towards the device.
    Inbound,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AttemptOutcome {
    NotApplicable,
    Established,
    Rejected,
    Unanswered,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MicroFlow {
    pub first_packet_us: i64,
    pub last_packet_us: i64,
    pub direction: Direction,
    pub packets_from_device: u32,
    pub packets_to_device: u32,
    pub bytes_from_device: u64,
    pub bytes_to_device: u64,
    pub tcp_syn: u32,
    pub tcp_syn_ack: u32,
    pub tcp_rst: u32,
    pub tcp_fin: u32,
    pub dns_query: Option<String>,
    initiating_syn: bool,
    established: bool,
    rejected: bool,
}

/// One packet's contribution to a micro-flow.
#[derive(Clone, Debug)]
pub struct Observation {
    pub key: FlowKey,
    pub timestamp_us: i64,
    pub from_device: bool,
    pub bytes: u64,
    pub tcp_flags: u8,
    /// An ICMP unreachable for this 5-tuple, sent by the responder.
    pub rejection: bool,
    pub dns_query: Option<String>,
}

impl MicroFlow {
    fn new(obs: &Observation, known_direction: Option<Direction>) -> Self {
        let sender = if obs.from_device {
            Direction::Outbound
        } else {
            Direction::Inbound
        };
        let syn = obs.tcp_flags & TCP_SYN != 0;
        let ack = obs.tcp_flags & TCP_ACK != 0;
        let direction = match known_direction {
            Some(direction) => direction,
            // A SYN-ACK answers an attempt made by the other side.
            None if syn && ack => opposite(sender),
            // ICMP errors are sent by the responder.
            None if obs.rejection => opposite(sender),
            None => sender,
        };
        Self {
            first_packet_us: obs.timestamp_us,
            last_packet_us: obs.timestamp_us,
            direction,
            packets_from_device: 0,
            packets_to_device: 0,
            bytes_from_device: 0,
            bytes_to_device: 0,
            tcp_syn: 0,
            tcp_syn_ack: 0,
            tcp_rst: 0,
            tcp_fin: 0,
            dns_query: None,
            initiating_syn: false,
            established: false,
            rejected: false,
        }
    }

    fn record(&mut self, obs: &Observation) {
        self.first_packet_us = self.first_packet_us.min(obs.timestamp_us);
        self.last_packet_us = self.last_packet_us.max(obs.timestamp_us);
        if obs.from_device {
            self.packets_from_device = self.packets_from_device.saturating_add(1);
            self.bytes_from_device = self.bytes_from_device.saturating_add(obs.bytes);
        } else {
            self.packets_to_device = self.packets_to_device.saturating_add(1);
            self.bytes_to_device = self.bytes_to_device.saturating_add(obs.bytes);
        }
        let from_initiator = obs.from_device == (self.direction == Direction::Outbound);
        let flags = obs.tcp_flags;
        if flags & TCP_SYN != 0 && flags & TCP_ACK != 0 {
            self.tcp_syn_ack += 1;
            if !from_initiator {
                self.established = true;
            }
        } else if flags & TCP_SYN != 0 {
            self.tcp_syn += 1;
            if from_initiator {
                self.initiating_syn = true;
            }
        }
        if flags & TCP_RST != 0 {
            self.tcp_rst += 1;
            if !from_initiator {
                self.rejected = true;
            }
        }
        if flags & TCP_FIN != 0 {
            self.tcp_fin += 1;
        }
        if obs.rejection {
            self.rejected = true;
        }
        if self.dns_query.is_none() {
            self.dns_query = obs.dns_query.clone();
        }
    }

    pub fn attempt_outcome(&self, protocol: Protocol) -> AttemptOutcome {
        if self.established {
            AttemptOutcome::Established
        } else if self.rejected {
            AttemptOutcome::Rejected
        } else if protocol == Protocol::Tcp && self.initiating_syn {
            AttemptOutcome::Unanswered
        } else {
            AttemptOutcome::NotApplicable
        }
    }
}

fn opposite(direction: Direction) -> Direction {
    match direction {
        Direction::Outbound => Direction::Inbound,
        Direction::Inbound => Direction::Outbound,
    }
}

/// A flushed micro-flow, ready to be encoded as a `FlowEvent`.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FlowRecord {
    pub key: FlowKey,
    pub flow: MicroFlow,
    pub attempt_outcome: AttemptOutcome,
    /// An earlier record already reported this 5-tuple.
    pub continuation: bool,
}

impl FlowRecord {
    /// One JSON object per line, used by the offline/stdout output until the
    /// Protobuf encoder replaces it.
    pub fn to_json(&self) -> String {
        let (src_ip, dst_ip, src_port, dst_port) = match self.flow.direction {
            Direction::Outbound => (
                self.key.device_ip,
                self.key.remote_ip,
                self.key.device_port,
                self.key.remote_port,
            ),
            Direction::Inbound => (
                self.key.remote_ip,
                self.key.device_ip,
                self.key.remote_port,
                self.key.device_port,
            ),
        };
        let dns = match &self.flow.dns_query {
            Some(q) => format!("\"{q}\""),
            None => "null".to_string(),
        };
        format!(
            concat!(
                "{{\"device_mac\":\"{}\",\"src_ip\":\"{}\",\"dst_ip\":\"{}\",",
                "\"src_port\":{},\"dst_port\":{},\"protocol\":\"{:?}\",\"direction\":\"{:?}\",",
                "\"first_packet_time_unix_micros\":{},\"last_packet_time_unix_micros\":{},",
                "\"packets_from_device\":{},\"packets_to_device\":{},",
                "\"bytes_from_device\":{},\"bytes_to_device\":{},",
                "\"tcp_syn\":{},\"tcp_syn_ack\":{},\"tcp_rst\":{},\"tcp_fin\":{},",
                "\"attempt_outcome\":\"{:?}\",\"dns_query\":{},\"continuation\":{}}}"
            ),
            format_mac(&self.key.device_mac),
            src_ip,
            dst_ip,
            src_port,
            dst_port,
            self.key.protocol,
            self.flow.direction,
            self.flow.first_packet_us,
            self.flow.last_packet_us,
            self.flow.packets_from_device,
            self.flow.packets_to_device,
            self.flow.bytes_from_device,
            self.flow.bytes_to_device,
            self.flow.tcp_syn,
            self.flow.tcp_syn_ack,
            self.flow.tcp_rst,
            self.flow.tcp_fin,
            self.attempt_outcome,
            dns,
            self.continuation,
        )
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum UpsertResult {
    Updated,
    Inserted,
    /// The table was full; the flow with the oldest last packet was flushed
    /// early to make room. Callers must count this as a health event.
    Evicted(FlowKey),
}

/// Bounded table of open micro-flows.
pub struct FlowTable {
    capacity: usize,
    idle_us: i64,
    active_us: i64,
    entries: HashMap<FlowKey, MicroFlow>,
    recent: HashMap<FlowKey, (Direction, i64)>,
}

impl FlowTable {
    pub fn new(capacity: usize) -> Self {
        Self::with_timeouts(capacity, DEFAULT_IDLE_US, DEFAULT_ACTIVE_US)
    }

    pub fn with_timeouts(capacity: usize, idle_us: i64, active_us: i64) -> Self {
        assert!(capacity > 0, "flow table capacity must be positive");
        Self {
            capacity,
            idle_us,
            active_us,
            entries: HashMap::new(),
            recent: HashMap::new(),
        }
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn get(&self, key: &FlowKey) -> Option<&MicroFlow> {
        self.entries.get(key)
    }

    /// Adds one observation. Flows closed as a side effect (a due flow for the
    /// same key, or an evicted one) are appended to `out`.
    pub fn upsert(&mut self, obs: Observation, out: &mut Vec<FlowRecord>) -> UpsertResult {
        if let Some(flow) = self.entries.get(&obs.key) {
            let due = obs.timestamp_us - flow.first_packet_us >= self.active_us
                || obs.timestamp_us - flow.last_packet_us >= self.idle_us;
            if !due {
                let flow = self.entries.get_mut(&obs.key).expect("checked above");
                flow.record(&obs);
                return UpsertResult::Updated;
            }
            let flow = self.entries.remove(&obs.key).expect("checked above");
            out.push(self.close(obs.key.clone(), flow));
        }

        let evicted = if self.entries.len() >= self.capacity {
            let oldest = self
                .entries
                .iter()
                .min_by(|a, b| (a.1.last_packet_us, a.0).cmp(&(b.1.last_packet_us, b.0)))
                .map(|(key, _)| key.clone());
            oldest.inspect(|key| {
                let flow = self.entries.remove(key).expect("key from map");
                out.push(self.close(key.clone(), flow));
            })
        } else {
            None
        };

        let known_direction = self.recent.get(&obs.key).map(|(direction, _)| *direction);
        let mut flow = MicroFlow::new(&obs, known_direction);
        flow.record(&obs);
        self.entries.insert(obs.key, flow);
        evicted.map_or(UpsertResult::Inserted, UpsertResult::Evicted)
    }

    /// Flushes every flow that is idle or has been open for the active timeout
    /// at `now_us`, oldest first.
    pub fn flush_due(&mut self, now_us: i64, out: &mut Vec<FlowRecord>) {
        let mut due: Vec<FlowKey> = self
            .entries
            .iter()
            .filter(|(_, flow)| {
                now_us - flow.last_packet_us >= self.idle_us
                    || now_us - flow.first_packet_us >= self.active_us
            })
            .map(|(key, _)| key.clone())
            .collect();
        self.drain(&mut due, out);
        self.recent
            .retain(|_, (_, flushed_us)| now_us - *flushed_us < CONTINUATION_TTL_US);
    }

    /// Flushes everything, e.g. at the end of an offline replay or shutdown.
    pub fn flush_all(&mut self, out: &mut Vec<FlowRecord>) {
        let mut keys: Vec<FlowKey> = self.entries.keys().cloned().collect();
        self.drain(&mut keys, out);
    }

    fn drain(&mut self, keys: &mut [FlowKey], out: &mut Vec<FlowRecord>) {
        keys.sort_by(|a, b| {
            let fa = self.entries[a].first_packet_us;
            let fb = self.entries[b].first_packet_us;
            (fa, a).cmp(&(fb, b))
        });
        for key in keys.iter() {
            let flow = self.entries.remove(key).expect("key from map");
            out.push(self.close(key.clone(), flow));
        }
    }

    fn close(&mut self, key: FlowKey, flow: MicroFlow) -> FlowRecord {
        let continuation = self
            .recent
            .insert(key.clone(), (flow.direction, flow.last_packet_us))
            .is_some();
        FlowRecord {
            attempt_outcome: flow.attempt_outcome(key.protocol),
            key,
            flow,
            continuation,
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SensorStats {
    pub frames: u64,
    pub non_ip: u64,
    pub malformed: u64,
    pub unattributed: u64,
    pub first_unattributed_us: Option<i64>,
    pub last_unattributed_us: Option<i64>,
    pub evictions: u64,
    pub flows: u64,
}

/// Frames in, attributed micro-flow records out.
pub struct Sensor {
    clients: Clients,
    table: FlowTable,
    stats: SensorStats,
}

impl Sensor {
    pub fn new(clients: Clients, table: FlowTable) -> Self {
        Self {
            clients,
            table,
            stats: SensorStats::default(),
        }
    }

    pub fn set_clients(&mut self, clients: Clients) {
        self.clients = clients;
    }

    pub fn stats(&self) -> &SensorStats {
        &self.stats
    }

    pub fn open_flows(&self) -> usize {
        self.table.len()
    }

    pub fn process_frame(
        &mut self,
        timestamp_us: i64,
        data: &[u8],
        wire_len: u32,
        out: &mut Vec<FlowRecord>,
    ) {
        self.stats.frames += 1;
        let packet = match parse_ethernet(data, wire_len) {
            Ok(packet) => packet,
            Err(ParseError::NotIp) => {
                self.stats.non_ip += 1;
                return;
            }
            Err(_) => {
                self.stats.malformed += 1;
                return;
            }
        };
        let (device_mac, from_device) =
            match self.clients.attribute(&packet.src_mac, &packet.dst_mac) {
                Attribution::FromDevice(mac) => (mac, true),
                Attribution::ToDevice(mac) => (mac, false),
                Attribution::Unattributed => {
                    self.stats.unattributed += 1;
                    self.stats.first_unattributed_us.get_or_insert(timestamp_us);
                    self.stats.last_unattributed_us = Some(timestamp_us);
                    return;
                }
            };

        // An ICMP unreachable is evidence about the flow it quotes, not a flow
        // of its own. The quoted packet travelled the opposite way.
        let (tuple, rejection, from_device_for_key) = match &packet.icmp {
            Some(Icmp::Unreachable(quoted)) => (*quoted, true, !from_device),
            _ => (packet.tuple, false, from_device),
        };
        let key = if from_device_for_key {
            FlowKey {
                device_mac,
                device_ip: tuple.src_ip,
                remote_ip: tuple.dst_ip,
                device_port: tuple.src_port,
                remote_port: tuple.dst_port,
                protocol: tuple.protocol,
            }
        } else {
            FlowKey {
                device_mac,
                device_ip: tuple.dst_ip,
                remote_ip: tuple.src_ip,
                device_port: tuple.dst_port,
                remote_port: tuple.src_port,
                protocol: tuple.protocol,
            }
        };
        let obs = Observation {
            key,
            timestamp_us,
            from_device,
            bytes: u64::from(packet.wire_len),
            tcp_flags: packet.tcp_flags,
            rejection,
            dns_query: packet.dns_query.filter(|_| from_device),
        };
        let before = out.len();
        if let UpsertResult::Evicted(_) = self.table.upsert(obs, out) {
            self.stats.evictions += 1;
        }
        self.stats.flows += (out.len() - before) as u64;
    }

    pub fn flush_due(&mut self, now_us: i64, out: &mut Vec<FlowRecord>) {
        let before = out.len();
        self.table.flush_due(now_us, out);
        self.stats.flows += (out.len() - before) as u64;
    }

    pub fn flush_all(&mut self, out: &mut Vec<FlowRecord>) {
        let before = out.len();
        self.table.flush_all(out);
        self.stats.flows += (out.len() - before) as u64;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Ipv4Addr;

    const DEVICE: Mac = [0x02, 0, 0, 0, 0, 0x0a];

    fn key(port: u16) -> FlowKey {
        FlowKey {
            device_mac: DEVICE,
            device_ip: IpAddr::V4(Ipv4Addr::new(192, 168, 50, 10)),
            remote_ip: IpAddr::V4(Ipv4Addr::new(203, 0, 113, 10)),
            device_port: 40000,
            remote_port: port,
            protocol: Protocol::Tcp,
        }
    }

    fn obs(port: u16, ts: i64, from_device: bool, flags: u8) -> Observation {
        Observation {
            key: key(port),
            timestamp_us: ts,
            from_device,
            bytes: 60,
            tcp_flags: flags,
            rejection: false,
            dns_query: None,
        }
    }

    #[test]
    fn idle_and_active_timeouts_bound_aggregation_latency() {
        let mut table = FlowTable::new(16);
        let mut out = Vec::new();
        for i in 0..15 {
            table.upsert(obs(443, i * 100_000, true, TCP_ACK), &mut out);
        }
        // The 11th packet (t = 1.0 s) closed the first micro-flow.
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].flow.packets_from_device, 10);
        assert!(!out[0].continuation);

        table.flush_due(1_400_000 + DEFAULT_IDLE_US, &mut out);
        assert_eq!(out.len(), 2);
        assert!(out[1].continuation);
        assert!(table.is_empty());
    }

    #[test]
    fn bounds_memory_and_flushes_the_oldest_flow_on_overflow() {
        let mut table = FlowTable::new(1);
        let mut out = Vec::new();
        table.upsert(obs(80, 1, true, TCP_SYN), &mut out);
        let result = table.upsert(obs(443, 2, true, TCP_SYN), &mut out);

        assert_eq!(result, UpsertResult::Evicted(key(80)));
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].key, key(80));
        assert_eq!(table.len(), 1);
    }

    #[test]
    fn late_syn_ack_reuses_the_remembered_direction() {
        let mut table = FlowTable::new(4);
        let mut out = Vec::new();
        table.upsert(obs(22, 0, true, TCP_SYN), &mut out);
        table.flush_due(DEFAULT_IDLE_US, &mut out);
        assert_eq!(out[0].attempt_outcome, AttemptOutcome::Unanswered);

        table.upsert(obs(22, 1_500_000, false, TCP_SYN | TCP_ACK), &mut out);
        table.flush_all(&mut out);
        assert_eq!(out[1].flow.direction, Direction::Outbound);
        assert_eq!(out[1].attempt_outcome, AttemptOutcome::Established);
        assert!(out[1].continuation);
    }
}
