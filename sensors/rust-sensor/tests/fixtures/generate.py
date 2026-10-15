#!/usr/bin/env python3
"""Regenerates the pcap fixtures used by tests/pcap_fixtures.rs.

Standard library only. Run from anywhere:

    python3 sensors/rust-sensor/tests/fixtures/generate.py

Addresses: AP subnet 192.168.50.0/24, gateway 192.168.50.1 (MAC ...:01),
device 192.168.50.10 (MAC ...:0a, listed in leases.txt), remote host
203.0.113.10. Timestamps start at 1_760_000_000 s so tests can check that
records carry packet time, not wall-clock time.
"""

import pathlib
import struct

HERE = pathlib.Path(__file__).resolve().parent
T0 = 1_760_000_000.0

GW_MAC = bytes.fromhex("020000000001")
DEV_MAC = bytes.fromhex("02000000000a")
UNKNOWN_A = bytes.fromhex("020000000099")
UNKNOWN_B = bytes.fromhex("020000000098")
GW_IP = "192.168.50.1"
DEV_IP = "192.168.50.10"
REMOTE_IP = "203.0.113.10"

FIN, SYN, RST, PSH, ACK = 0x01, 0x02, 0x04, 0x08, 0x10


def ip4(addr):
    return bytes(int(part) for part in addr.split("."))


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def ipv4(src, dst, proto, payload, ident=1):
    header = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident, 0, 64, proto, 0, ip4(src), ip4(dst)
    )
    header = header[:10] + struct.pack("!H", checksum(header)) + header[12:]
    return header + payload


def pseudo(src, dst, proto, length):
    return ip4(src) + ip4(dst) + struct.pack("!BBH", 0, proto, length)


def tcp(src, dst, sport, dport, flags, payload=b"", seq=1000, ack=0):
    header = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, 5 << 4, flags, 64240, 0, 0)
    csum = checksum(pseudo(src, dst, 6, len(header) + len(payload)) + header + payload)
    header = header[:16] + struct.pack("!H", csum) + header[18:]
    return ipv4(src, dst, 6, header + payload)


def udp(src, dst, sport, dport, payload):
    header = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0)
    csum = checksum(pseudo(src, dst, 17, len(header) + len(payload)) + header + payload) or 0xFFFF
    header = header[:6] + struct.pack("!H", csum)
    return ipv4(src, dst, 17, header + payload)


def icmp(src, dst, kind, code, rest, body):
    message = struct.pack("!BBH4s", kind, code, 0, rest) + body
    message = message[:2] + struct.pack("!H", checksum(message)) + message[4:]
    return ipv4(src, dst, 1, message)


def ether(src_mac, dst_mac, ip_packet):
    return dst_mac + src_mac + b"\x08\x00" + ip_packet


def dns_query(name, ident=0x1234):
    question = b"".join(bytes([len(label)]) + label.encode() for label in name.split("."))
    return struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0) + question + b"\0" + struct.pack("!HH", 1, 1)


def dns_response(name, ident=0x1234):
    query = dns_query(name, ident)
    answer = struct.pack("!HHHIH4s", 0xC00C, 1, 1, 60, 4, ip4(REMOTE_IP))
    return query[:2] + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0) + query[12:] + answer


def from_device(packet):
    return ether(DEV_MAC, GW_MAC, packet)


def to_device(packet):
    return ether(GW_MAC, DEV_MAC, packet)


def write_pcap(name, frames):
    out = bytearray(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
    for offset, frame in frames:
        ts = T0 + offset
        secs = int(ts)
        micros = round((ts - secs) * 1_000_000)
        out += struct.pack("<IIII", secs, micros, len(frame), len(frame)) + frame
    (HERE / name).write_bytes(bytes(out))


def main():
    d, r = DEV_IP, REMOTE_IP
    write_pcap("tcp_established.pcap", [
        (0.000, from_device(tcp(d, r, 40000, 443, SYN, seq=1000))),
        (0.010, to_device(tcp(r, d, 443, 40000, SYN | ACK, seq=5000, ack=1001))),
        (0.011, from_device(tcp(d, r, 40000, 443, ACK, seq=1001, ack=5001))),
        (0.020, from_device(tcp(d, r, 40000, 443, PSH | ACK, b"x" * 100, seq=1001, ack=5001))),
        (0.030, from_device(tcp(d, r, 40000, 443, FIN | ACK, seq=1101, ack=5001))),
        (0.040, to_device(tcp(r, d, 443, 40000, FIN | ACK, seq=5001, ack=1102))),
        (0.041, from_device(tcp(d, r, 40000, 443, ACK, seq=1102, ack=5002))),
    ])
    write_pcap("tcp_rejected.pcap", [
        (0.000, from_device(tcp(d, r, 40001, 23, SYN))),
        (0.005, to_device(tcp(r, d, 23, 40001, RST | ACK, seq=0, ack=1001))),
    ])
    # The retransmission lands after the active timeout: two micro-flows.
    write_pcap("tcp_unanswered.pcap", [
        (0.000, from_device(tcp(d, r, 40002, 8080, SYN))),
        (1.200, from_device(tcp(d, r, 40002, 8080, SYN))),
    ])
    write_pcap("udp_dns.pcap", [
        (0.000, from_device(udp(d, GW_IP, 53000, 53, dns_query("Example.com")))),
        (0.002, to_device(udp(GW_IP, d, 53, 53000, dns_response("example.com")))),
    ])
    probe = udp(d, r, 41000, 161, b"\x30\x00")
    write_pcap("icmp.pcap", [
        (0.000, from_device(icmp(d, r, 8, 0, struct.pack("!HH", 7, 1), b"ping"))),
        (0.004, to_device(icmp(r, d, 0, 0, struct.pack("!HH", 7, 1), b"ping"))),
        (0.100, from_device(probe)),
        (0.103, to_device(icmp(r, d, 3, 3, b"\0\0\0\0", probe[:28]))),
    ])
    write_pcap("unattributed.pcap", [
        (0.000, ether(UNKNOWN_A, UNKNOWN_B, tcp("192.168.50.99", r, 40003, 80, SYN))),
        (0.500, ether(UNKNOWN_B, UNKNOWN_A, tcp(r, "192.168.50.99", 80, 40003, RST | ACK))),
        (0.600, from_device(tcp(d, r, 40004, 80, SYN))),
    ])
    (HERE / "leases.txt").write_text(
        "1760003600 02:00:00:00:00:0a 192.168.50.10 sensor-a 01:02:00:00:00:00:0a\n"
    )


if __name__ == "__main__":
    main()
