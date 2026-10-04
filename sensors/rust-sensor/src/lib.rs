//! Deterministic, bounded flow state for the gateway sensor.
//!
//! Capture backends and Kafka publishing are intentionally outside this crate.
//! Keeping the state machine dependency-free makes it testable on a laptop and
//! on ARM64 before selecting the final packet-capture implementation.

use std::collections::BTreeMap;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct FlowKey {
    pub device_key: String,
    pub src_ip: String,
    pub dst_ip: String,
    pub src_port: u16,
    pub dst_port: u16,
    pub protocol: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct FlowAccumulator {
    pub first_seen_ms: u64,
    pub last_seen_ms: u64,
    pub bytes_sent: u64,
    pub bytes_received: u64,
    pub packets: u32,
}

impl FlowAccumulator {
    pub fn record(&mut self, event_time_ms: u64, bytes_sent: u64, bytes_received: u64) {
        if self.packets == 0 {
            self.first_seen_ms = event_time_ms;
        } else {
            self.first_seen_ms = self.first_seen_ms.min(event_time_ms);
        }
        self.last_seen_ms = self.last_seen_ms.max(event_time_ms);
        self.bytes_sent = self.bytes_sent.saturating_add(bytes_sent);
        self.bytes_received = self.bytes_received.saturating_add(bytes_received);
        self.packets = self.packets.saturating_add(1);
    }

    pub fn duration_ms(&self) -> u32 {
        self.last_seen_ms
            .saturating_sub(self.first_seen_ms)
            .min(u32::MAX as u64) as u32
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum UpsertResult {
    Updated,
    Inserted,
    Evicted(FlowKey),
}

/// A bounded flow table. The deterministic key order makes overflow behavior
/// reproducible until a timestamp-aware eviction policy is added.
pub struct FlowTable {
    capacity: usize,
    entries: BTreeMap<FlowKey, FlowAccumulator>,
}

impl FlowTable {
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "flow table capacity must be positive");
        Self {
            capacity,
            entries: BTreeMap::new(),
        }
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn upsert(
        &mut self,
        key: FlowKey,
        event_time_ms: u64,
        bytes_sent: u64,
        bytes_received: u64,
    ) -> UpsertResult {
        if let Some(flow) = self.entries.get_mut(&key) {
            flow.record(event_time_ms, bytes_sent, bytes_received);
            return UpsertResult::Updated;
        }

        let evicted = if self.entries.len() >= self.capacity {
            let oldest_key = self.entries.keys().next().cloned();
            oldest_key.and_then(|key| self.entries.remove(&key).map(|_| key))
        } else {
            None
        };

        let mut flow = FlowAccumulator::default();
        flow.record(event_time_ms, bytes_sent, bytes_received);
        self.entries.insert(key, flow);
        evicted.map_or(UpsertResult::Inserted, UpsertResult::Evicted)
    }

    pub fn get(&self, key: &FlowKey) -> Option<&FlowAccumulator> {
        self.entries.get(key)
    }

    pub fn expire_before(&mut self, event_time_ms: u64) -> Vec<(FlowKey, FlowAccumulator)> {
        let expired_keys: Vec<FlowKey> = self
            .entries
            .iter()
            .filter(|(_, flow)| flow.last_seen_ms < event_time_ms)
            .map(|(key, _)| key.clone())
            .collect();

        expired_keys
            .into_iter()
            .filter_map(|key| self.entries.remove(&key).map(|flow| (key, flow)))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(device: &str, port: u16) -> FlowKey {
        FlowKey {
            device_key: device.to_string(),
            src_ip: "192.168.50.10".to_string(),
            dst_ip: "203.0.113.10".to_string(),
            src_port: 40000,
            dst_port: port,
            protocol: "TCP".to_string(),
        }
    }

    #[test]
    fn aggregates_packets_and_preserves_event_time_bounds() {
        let mut table = FlowTable::new(4);
        let flow_key = key("device-a", 443);
        assert_eq!(
            table.upsert(flow_key.clone(), 200, 100, 10),
            UpsertResult::Inserted
        );
        assert_eq!(
            table.upsert(flow_key.clone(), 100, 50, 5),
            UpsertResult::Updated
        );

        let flow = table.get(&flow_key).expect("flow exists");
        assert_eq!(flow.first_seen_ms, 100);
        assert_eq!(flow.last_seen_ms, 200);
        assert_eq!(flow.bytes_sent, 150);
        assert_eq!(flow.bytes_received, 15);
        assert_eq!(flow.packets, 2);
        assert_eq!(flow.duration_ms(), 100);
    }

    #[test]
    fn bounds_memory_and_reports_eviction() {
        let mut table = FlowTable::new(1);
        table.upsert(key("device-a", 80), 1, 1, 0);
        let result = table.upsert(key("device-b", 443), 2, 1, 0);

        assert_eq!(result, UpsertResult::Evicted(key("device-a", 80)));
        assert_eq!(table.len(), 1);
        assert!(table.get(&key("device-b", 443)).is_some());
    }

    #[test]
    fn expires_only_flows_older_than_watermark() {
        let mut table = FlowTable::new(4);
        table.upsert(key("old", 80), 100, 1, 0);
        table.upsert(key("new", 443), 200, 1, 0);

        let expired = table.expire_before(200);
        assert_eq!(expired.len(), 1);
        assert_eq!(expired[0].0, key("old", 80));
        assert_eq!(table.len(), 1);
    }
}
