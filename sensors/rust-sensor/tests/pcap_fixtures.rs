//! Replays the pcap fixtures (see tests/fixtures/generate.py) through the
//! sensor core and checks the resulting micro-flows.

use std::net::{IpAddr, Ipv4Addr};
use std::path::PathBuf;

use pi_agents_sensor::attribution::Clients;
use pi_agents_sensor::packet::Protocol;
use pi_agents_sensor::pcapfile::PcapReader;
use pi_agents_sensor::{AttemptOutcome, Direction, FlowRecord, FlowTable, Sensor, SensorStats};

const T0_US: i64 = 1_760_000_000_000_000;
const DEVICE_MAC: [u8; 6] = [0x02, 0, 0, 0, 0, 0x0a];

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
}

fn replay(name: &str) -> (Vec<FlowRecord>, SensorStats) {
    let leases = std::fs::read_to_string(fixture("leases.txt")).expect("leases fixture");
    let bytes = std::fs::read(fixture(name)).expect("pcap fixture");
    let mut sensor = Sensor::new(Clients::from_dnsmasq_leases(&leases), FlowTable::new(1024));
    let mut out = Vec::new();
    for record in PcapReader::new(&bytes).expect("pcap header") {
        let record = record.expect("pcap record");
        sensor.flush_due(record.timestamp_us, &mut out);
        sensor.process_frame(record.timestamp_us, record.data, record.wire_len, &mut out);
    }
    sensor.flush_all(&mut out);
    (out, sensor.stats().clone())
}

fn ip(a: u8, b: u8, c: u8, d: u8) -> IpAddr {
    IpAddr::V4(Ipv4Addr::new(a, b, c, d))
}

#[test]
fn established_tcp_connection_is_one_outbound_micro_flow() {
    let (flows, stats) = replay("tcp_established.pcap");
    assert_eq!(flows.len(), 1);
    let f = &flows[0];
    assert_eq!(f.key.device_mac, DEVICE_MAC);
    assert_eq!(f.key.device_ip, ip(192, 168, 50, 10));
    assert_eq!(f.key.remote_ip, ip(203, 0, 113, 10));
    assert_eq!((f.key.device_port, f.key.remote_port), (40000, 443));
    assert_eq!(f.key.protocol, Protocol::Tcp);
    assert_eq!(f.flow.direction, Direction::Outbound);
    assert_eq!(f.attempt_outcome, AttemptOutcome::Established);
    assert_eq!(
        (f.flow.packets_from_device, f.flow.packets_to_device),
        (5, 2)
    );
    assert_eq!(
        (f.flow.bytes_from_device, f.flow.bytes_to_device),
        (54 * 4 + 154, 54 * 2)
    );
    assert_eq!(
        (
            f.flow.tcp_syn,
            f.flow.tcp_syn_ack,
            f.flow.tcp_rst,
            f.flow.tcp_fin
        ),
        (1, 1, 0, 2)
    );
    // Packet time from the capture, not wall-clock time.
    assert_eq!(f.flow.first_packet_us, T0_US);
    assert_eq!(f.flow.last_packet_us, T0_US + 41_000);
    assert!(!f.continuation);
    assert_eq!(stats.unattributed, 0);
}

#[test]
fn rst_reply_marks_the_attempt_rejected() {
    let (flows, _) = replay("tcp_rejected.pcap");
    assert_eq!(flows.len(), 1);
    assert_eq!(flows[0].key.remote_port, 23);
    assert_eq!(flows[0].flow.direction, Direction::Outbound);
    assert_eq!(flows[0].attempt_outcome, AttemptOutcome::Rejected);
    assert_eq!(flows[0].flow.tcp_rst, 1);
}

#[test]
fn unanswered_syn_and_its_retransmission_are_flushed_separately() {
    let (flows, _) = replay("tcp_unanswered.pcap");
    assert_eq!(flows.len(), 2);
    for f in &flows {
        assert_eq!(f.key.remote_port, 8080);
        assert_eq!(f.attempt_outcome, AttemptOutcome::Unanswered);
        assert_eq!(f.flow.packets_from_device, 1);
    }
    assert!(!flows[0].continuation);
    assert!(flows[1].continuation);
    assert_eq!(flows[1].flow.first_packet_us, T0_US + 1_200_000);
}

#[test]
fn dns_query_name_is_recorded_on_the_udp_flow() {
    let (flows, _) = replay("udp_dns.pcap");
    assert_eq!(flows.len(), 1);
    let f = &flows[0];
    assert_eq!(f.key.protocol, Protocol::Udp);
    assert_eq!(f.key.remote_ip, ip(192, 168, 50, 1));
    assert_eq!(f.key.remote_port, 53);
    assert_eq!(f.flow.dns_query.as_deref(), Some("example.com"));
    assert_eq!(
        (f.flow.packets_from_device, f.flow.packets_to_device),
        (1, 1)
    );
    assert_eq!(f.attempt_outcome, AttemptOutcome::NotApplicable);
}

#[test]
fn icmp_echo_is_a_flow_and_port_unreachable_rejects_the_probe() {
    let (flows, _) = replay("icmp.pcap");
    assert_eq!(flows.len(), 2);

    let echo = &flows[0];
    assert_eq!(echo.key.protocol, Protocol::Icmp);
    assert_eq!(
        (echo.flow.packets_from_device, echo.flow.packets_to_device),
        (1, 1)
    );
    assert_eq!(echo.attempt_outcome, AttemptOutcome::NotApplicable);

    // The ICMP error is folded into the UDP flow it quotes.
    let probe = &flows[1];
    assert_eq!(probe.key.protocol, Protocol::Udp);
    assert_eq!((probe.key.device_port, probe.key.remote_port), (41000, 161));
    assert_eq!(probe.flow.direction, Direction::Outbound);
    assert_eq!(probe.attempt_outcome, AttemptOutcome::Rejected);
    assert_eq!(
        (probe.flow.packets_from_device, probe.flow.packets_to_device),
        (1, 1)
    );
}

#[test]
fn traffic_without_a_known_client_mac_is_counted_not_emitted() {
    let (flows, stats) = replay("unattributed.pcap");
    assert_eq!(flows.len(), 1);
    assert_eq!(flows[0].key.remote_port, 80);
    assert_eq!(stats.unattributed, 2);
    assert_eq!(stats.first_unattributed_us, Some(T0_US));
    assert_eq!(stats.last_unattributed_us, Some(T0_US + 500_000));
    assert_eq!(stats.frames, 3);
}

#[test]
fn json_output_orients_addresses_by_direction() {
    let (flows, _) = replay("tcp_rejected.pcap");
    let json = flows[0].to_json();
    assert!(json.contains("\"device_mac\":\"02:00:00:00:00:0a\""));
    assert!(json.contains("\"src_ip\":\"192.168.50.10\""));
    assert!(json.contains("\"dst_port\":23"));
    assert!(json.contains("\"attempt_outcome\":\"Rejected\""));
}
