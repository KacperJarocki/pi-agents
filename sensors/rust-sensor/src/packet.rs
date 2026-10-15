//! Minimal, bounds-checked parser for the frames the gateway sees on its AP
//! interface: Ethernet II (optionally one 802.1Q tag), IPv4/IPv6, and
//! TCP/UDP/ICMP/ICMPv6. Anything else is reported as `Protocol::Other` or
//! rejected with a `ParseError`; the parser never panics on malformed input.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

pub type Mac = [u8; 6];

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum Protocol {
    Tcp,
    Udp,
    Icmp,
    Icmpv6,
    Other(u8),
}

pub const TCP_FIN: u8 = 0x01;
pub const TCP_SYN: u8 = 0x02;
pub const TCP_RST: u8 = 0x04;
pub const TCP_ACK: u8 = 0x10;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct FiveTuple {
    pub src_ip: IpAddr,
    pub dst_ip: IpAddr,
    pub src_port: u16,
    pub dst_port: u16,
    pub protocol: Protocol,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Icmp {
    EchoRequest,
    EchoReply,
    /// Destination unreachable, carrying the 5-tuple of the packet it rejects.
    Unreachable(FiveTuple),
    Other,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ParsedPacket {
    pub src_mac: Mac,
    pub dst_mac: Mac,
    pub tuple: FiveTuple,
    pub tcp_flags: u8,
    /// Bytes on the wire for this frame (Ethernet header included).
    pub wire_len: u32,
    pub icmp: Option<Icmp>,
    /// First question of a DNS query (not response) sent to port 53.
    pub dns_query: Option<String>,
}

#[derive(Debug, Eq, PartialEq)]
pub enum ParseError {
    Truncated,
    NotIp,
    BadIpHeader,
}

const ETHERTYPE_IPV4: u16 = 0x0800;
const ETHERTYPE_IPV6: u16 = 0x86dd;
const ETHERTYPE_VLAN: u16 = 0x8100;

fn be16(b: &[u8], at: usize) -> Result<u16, ParseError> {
    b.get(at..at + 2)
        .map(|s| u16::from_be_bytes([s[0], s[1]]))
        .ok_or(ParseError::Truncated)
}

fn mac(b: &[u8], at: usize) -> Result<Mac, ParseError> {
    let s = b.get(at..at + 6).ok_or(ParseError::Truncated)?;
    let mut m = [0u8; 6];
    m.copy_from_slice(s);
    Ok(m)
}

pub fn format_mac(m: &Mac) -> String {
    format!(
        "{:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x}",
        m[0], m[1], m[2], m[3], m[4], m[5]
    )
}

pub fn parse_mac(s: &str) -> Option<Mac> {
    let mut m = [0u8; 6];
    let mut parts = s.trim().split(':');
    for byte in m.iter_mut() {
        *byte = u8::from_str_radix(parts.next()?, 16).ok()?;
    }
    parts.next().is_none().then_some(m)
}

/// Parses one Ethernet frame. `wire_len` is the original frame length, which
/// may exceed `frame.len()` when the capture snap length truncated it.
pub fn parse_ethernet(frame: &[u8], wire_len: u32) -> Result<ParsedPacket, ParseError> {
    let dst_mac = mac(frame, 0)?;
    let src_mac = mac(frame, 6)?;
    let mut ethertype = be16(frame, 12)?;
    let mut offset = 14;
    if ethertype == ETHERTYPE_VLAN {
        ethertype = be16(frame, 16)?;
        offset = 18;
    }
    let l3 = frame.get(offset..).ok_or(ParseError::Truncated)?;
    let ip = match ethertype {
        ETHERTYPE_IPV4 => parse_ipv4(l3)?,
        ETHERTYPE_IPV6 => parse_ipv6(l3)?,
        _ => return Err(ParseError::NotIp),
    };
    let mut packet = ParsedPacket {
        src_mac,
        dst_mac,
        tuple: FiveTuple {
            src_ip: ip.src,
            dst_ip: ip.dst,
            src_port: 0,
            dst_port: 0,
            protocol: ip.protocol,
        },
        tcp_flags: 0,
        wire_len,
        icmp: None,
        dns_query: None,
    };
    if ip.first_fragment {
        parse_transport(&mut packet, ip.payload);
    }
    Ok(packet)
}

struct IpLayer<'a> {
    src: IpAddr,
    dst: IpAddr,
    protocol: Protocol,
    /// False for non-first IPv4 fragments, which carry no transport header.
    first_fragment: bool,
    payload: &'a [u8],
}

fn protocol_from(number: u8) -> Protocol {
    match number {
        6 => Protocol::Tcp,
        17 => Protocol::Udp,
        1 => Protocol::Icmp,
        58 => Protocol::Icmpv6,
        other => Protocol::Other(other),
    }
}

fn parse_ipv4(b: &[u8]) -> Result<IpLayer<'_>, ParseError> {
    let first = *b.first().ok_or(ParseError::Truncated)?;
    if first >> 4 != 4 {
        return Err(ParseError::BadIpHeader);
    }
    let header_len = usize::from(first & 0x0f) * 4;
    if header_len < 20 || b.len() < header_len {
        return Err(ParseError::BadIpHeader);
    }
    let total_len = usize::from(be16(b, 2)?).clamp(header_len, b.len());
    let fragment_offset = be16(b, 6)? & 0x1fff;
    let src = Ipv4Addr::new(b[12], b[13], b[14], b[15]);
    let dst = Ipv4Addr::new(b[16], b[17], b[18], b[19]);
    Ok(IpLayer {
        src: IpAddr::V4(src),
        dst: IpAddr::V4(dst),
        protocol: protocol_from(b[9]),
        first_fragment: fragment_offset == 0,
        payload: &b[header_len..total_len],
    })
}

fn parse_ipv6(b: &[u8]) -> Result<IpLayer<'_>, ParseError> {
    if b.len() < 40 || b[0] >> 4 != 6 {
        return Err(ParseError::BadIpHeader);
    }
    let mut src = [0u8; 16];
    let mut dst = [0u8; 16];
    src.copy_from_slice(&b[8..24]);
    dst.copy_from_slice(&b[24..40]);
    let payload_len = usize::from(be16(b, 4)?);
    let end = (40 + payload_len).min(b.len());
    // Extension headers are not walked; such packets count as Other.
    Ok(IpLayer {
        src: IpAddr::V6(Ipv6Addr::from(src)),
        dst: IpAddr::V6(Ipv6Addr::from(dst)),
        protocol: protocol_from(b[6]),
        first_fragment: true,
        payload: &b[40..end],
    })
}

fn parse_transport(packet: &mut ParsedPacket, payload: &[u8]) {
    match packet.tuple.protocol {
        Protocol::Tcp => {
            if let (Ok(src), Ok(dst), Some(flags)) =
                (be16(payload, 0), be16(payload, 2), payload.get(13))
            {
                packet.tuple.src_port = src;
                packet.tuple.dst_port = dst;
                packet.tcp_flags = *flags;
            }
        }
        Protocol::Udp => {
            if let (Ok(src), Ok(dst)) = (be16(payload, 0), be16(payload, 2)) {
                packet.tuple.src_port = src;
                packet.tuple.dst_port = dst;
                if dst == 53 {
                    packet.dns_query = payload.get(8..).and_then(parse_dns_query);
                }
            }
        }
        Protocol::Icmp => packet.icmp = Some(parse_icmp(payload, false)),
        Protocol::Icmpv6 => packet.icmp = Some(parse_icmp(payload, true)),
        Protocol::Other(_) => {}
    }
}

fn parse_icmp(b: &[u8], v6: bool) -> Icmp {
    let (Some(&kind), Some(_code)) = (b.first(), b.get(1)) else {
        return Icmp::Other;
    };
    let (echo_request, echo_reply, unreachable) = if v6 { (128, 129, 1) } else { (8, 0, 3) };
    if kind == echo_request {
        Icmp::EchoRequest
    } else if kind == echo_reply {
        Icmp::EchoReply
    } else if kind == unreachable {
        // ICMP header (8 bytes), then the offending IP header + 8 bytes.
        let embedded = b.get(8..).unwrap_or(&[]);
        let ip = if v6 {
            parse_ipv6(embedded)
        } else {
            parse_ipv4_embedded(embedded)
        };
        match ip {
            Ok(ip) => {
                let ports = (be16(ip.payload, 0), be16(ip.payload, 2));
                let (src_port, dst_port) = match (ip.protocol, ports) {
                    (Protocol::Tcp | Protocol::Udp, (Ok(s), Ok(d))) => (s, d),
                    _ => (0, 0),
                };
                Icmp::Unreachable(FiveTuple {
                    src_ip: ip.src,
                    dst_ip: ip.dst,
                    src_port,
                    dst_port,
                    protocol: ip.protocol,
                })
            }
            Err(_) => Icmp::Other,
        }
    } else {
        Icmp::Other
    }
}

/// Embedded IPv4 headers in ICMP errors keep the original total length, which
/// is larger than what was quoted; read the quoted bytes as they are.
fn parse_ipv4_embedded(b: &[u8]) -> Result<IpLayer<'_>, ParseError> {
    let first = *b.first().ok_or(ParseError::Truncated)?;
    let header_len = usize::from(first & 0x0f) * 4;
    if first >> 4 != 4 || header_len < 20 || b.len() < header_len {
        return Err(ParseError::BadIpHeader);
    }
    Ok(IpLayer {
        src: IpAddr::V4(Ipv4Addr::new(b[12], b[13], b[14], b[15])),
        dst: IpAddr::V4(Ipv4Addr::new(b[16], b[17], b[18], b[19])),
        protocol: protocol_from(b[9]),
        first_fragment: true,
        payload: &b[header_len..],
    })
}

/// Returns the first question name of a DNS query message, lower-cased.
fn parse_dns_query(msg: &[u8]) -> Option<String> {
    let flags = be16(msg, 2).ok()?;
    let questions = be16(msg, 4).ok()?;
    if flags & 0x8000 != 0 || questions == 0 {
        return None;
    }
    let mut name = String::new();
    let mut at = 12;
    loop {
        let len = usize::from(*msg.get(at)?);
        if len == 0 {
            break;
        }
        // Compression pointers never appear in a query's first question.
        if len > 63 || name.len() + len > 253 {
            return None;
        }
        let label = msg.get(at + 1..at + 1 + len)?;
        if !name.is_empty() {
            name.push('.');
        }
        for &c in label {
            if !(c.is_ascii_alphanumeric() || c == b'-' || c == b'_') {
                return None;
            }
            name.push(c.to_ascii_lowercase() as char);
        }
        at += 1 + len;
    }
    (!name.is_empty()).then_some(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_short_and_non_ip_frames_without_panicking() {
        assert_eq!(parse_ethernet(&[0u8; 5], 5), Err(ParseError::Truncated));
        let mut arp = vec![0u8; 42];
        arp[12] = 0x08;
        arp[13] = 0x06;
        assert_eq!(parse_ethernet(&arp, 42), Err(ParseError::NotIp));
        for len in 0..64 {
            let mut frame = vec![0xffu8; len];
            if len > 13 {
                frame[12] = 0x08;
                frame[13] = 0x00;
            }
            let _ = parse_ethernet(&frame, len as u32);
        }
    }

    #[test]
    fn round_trips_mac_strings() {
        let m = parse_mac("AA:bb:0c:00:01:ff").expect("valid mac");
        assert_eq!(format_mac(&m), "aa:bb:0c:00:01:ff");
        assert_eq!(parse_mac("aa:bb:cc"), None);
        assert_eq!(parse_mac("aa:bb:cc:dd:ee:ff:00"), None);
    }

    #[test]
    fn reads_dns_query_name_and_ignores_responses() {
        let mut q = vec![0x12, 0x34, 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0];
        q.extend_from_slice(b"\x07Example\x03com\x00\x00\x01\x00\x01");
        assert_eq!(parse_dns_query(&q).as_deref(), Some("example.com"));
        q[2] = 0x81;
        assert_eq!(parse_dns_query(&q), None);
    }
}
