//! Gateway sensor binary. Prints one JSON micro-flow per line on stdout and a
//! stats line on stderr; the Kafka/Protobuf publisher replaces stdout later.
//!
//!   pi-agents-sensor --leases /data/dnsmasq.leases --pcap capture.pcap
//!   pi-agents-sensor --leases /data/dnsmasq.leases --interface wlan0   (feature "live")

use std::io::Write;
use std::process::ExitCode;

use pi_agents_sensor::attribution::Clients;
use pi_agents_sensor::pcapfile::PcapReader;
use pi_agents_sensor::{FlowRecord, FlowTable, Sensor};

struct Args {
    leases: String,
    pcap: Option<String>,
    interface: Option<String>,
    capacity: usize,
}

fn usage() -> ExitCode {
    eprintln!(
        "usage: pi-agents-sensor --leases <dnsmasq.leases> (--pcap <file> | --interface <iface>) [--capacity <flows>]"
    );
    ExitCode::from(2)
}

fn parse_args() -> Option<Args> {
    let mut args = Args {
        leases: String::new(),
        pcap: None,
        interface: None,
        capacity: 65_536,
    };
    let mut it = std::env::args().skip(1);
    while let Some(flag) = it.next() {
        let value = it.next()?;
        match flag.as_str() {
            "--leases" => args.leases = value,
            "--pcap" => args.pcap = Some(value),
            "--interface" => args.interface = Some(value),
            "--capacity" => args.capacity = value.parse().ok().filter(|c| *c > 0)?,
            _ => return None,
        }
    }
    let one_source = args.pcap.is_some() != args.interface.is_some();
    (!args.leases.is_empty() && one_source).then_some(args)
}

fn load_clients(path: &str) -> Clients {
    match std::fs::read_to_string(path) {
        Ok(contents) => Clients::from_dnsmasq_leases(&contents),
        Err(err) => {
            eprintln!("warning: cannot read leases {path}: {err}; all traffic is unattributed");
            Clients::default()
        }
    }
}

fn emit(out: &mut Vec<FlowRecord>) {
    let stdout = std::io::stdout();
    let mut lock = stdout.lock();
    for record in out.drain(..) {
        // A closed stdout ends the process; nothing else can be done with the record.
        if writeln!(lock, "{}", record.to_json()).is_err() {
            std::process::exit(0);
        }
    }
}

fn report(sensor: &Sensor) {
    let s = sensor.stats();
    eprintln!(
        "{{\"frames\":{},\"flows\":{},\"open_flows\":{},\"unattributed\":{},\"non_ip\":{},\"malformed\":{},\"evictions\":{}}}",
        s.frames,
        s.flows,
        sensor.open_flows(),
        s.unattributed,
        s.non_ip,
        s.malformed,
        s.evictions
    );
}

fn replay(path: &str, mut sensor: Sensor) -> ExitCode {
    let bytes = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(err) => {
            eprintln!("error: cannot read {path}: {err}");
            return ExitCode::FAILURE;
        }
    };
    let reader = match PcapReader::new(&bytes) {
        Ok(reader) => reader,
        Err(err) => {
            eprintln!("error: {path}: {err:?}");
            return ExitCode::FAILURE;
        }
    };
    let mut out = Vec::new();
    for record in reader {
        let Ok(record) = record else {
            eprintln!("warning: {path}: truncated record, stopping");
            break;
        };
        sensor.flush_due(record.timestamp_us, &mut out);
        sensor.process_frame(record.timestamp_us, record.data, record.wire_len, &mut out);
        emit(&mut out);
    }
    sensor.flush_all(&mut out);
    emit(&mut out);
    report(&sensor);
    ExitCode::SUCCESS
}

#[cfg(feature = "live")]
fn capture(interface: &str, leases: &str, mut sensor: Sensor) -> ExitCode {
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    let now_us = || {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_micros() as i64)
            .unwrap_or(0)
    };
    let modified = |path: &str| std::fs::metadata(path).and_then(|m| m.modified()).ok();

    let capture = pcap::Capture::from_device(interface).and_then(|c| {
        c.snaplen(512)
            .immediate_mode(true)
            .timeout(100)
            .buffer_size(8 << 20)
            .open()
    });
    let mut capture = match capture {
        Ok(capture) => capture,
        Err(err) => {
            eprintln!("error: cannot capture on {interface}: {err}");
            return ExitCode::FAILURE;
        }
    };

    let mut out = Vec::new();
    let mut leases_mtime = modified(leases);
    let mut last_flush = Instant::now();
    let mut last_housekeeping = Instant::now();
    loop {
        match capture.next_packet() {
            Ok(packet) => {
                let ts = packet.header.ts;
                // timeval field widths differ between Linux and macOS.
                #[allow(clippy::useless_conversion)]
                let timestamp_us = i64::from(ts.tv_sec) * 1_000_000 + i64::from(ts.tv_usec);
                sensor.process_frame(timestamp_us, packet.data, packet.header.len, &mut out);
            }
            Err(pcap::Error::TimeoutExpired) => {}
            Err(err) => {
                eprintln!("error: capture on {interface} failed: {err}");
                sensor.flush_all(&mut out);
                emit(&mut out);
                return ExitCode::FAILURE;
            }
        }
        if last_flush.elapsed() >= Duration::from_millis(100) {
            sensor.flush_due(now_us(), &mut out);
            last_flush = Instant::now();
        }
        emit(&mut out);
        if last_housekeeping.elapsed() >= Duration::from_secs(10) {
            let mtime = modified(leases);
            if mtime != leases_mtime {
                sensor.set_clients(load_clients(leases));
                leases_mtime = mtime;
            }
            report(&sensor);
            last_housekeeping = Instant::now();
        }
    }
}

#[cfg(not(feature = "live"))]
fn capture(_interface: &str, _leases: &str, _sensor: Sensor) -> ExitCode {
    eprintln!("error: live capture needs the `live` feature (cargo build --features live)");
    ExitCode::from(2)
}

fn main() -> ExitCode {
    let Some(args) = parse_args() else {
        return usage();
    };
    let sensor = Sensor::new(load_clients(&args.leases), FlowTable::new(args.capacity));
    match (&args.pcap, &args.interface) {
        (Some(path), None) => replay(path, sensor),
        (None, Some(interface)) => capture(interface, &args.leases, sensor),
        _ => usage(),
    }
}
