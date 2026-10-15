//! Gateway sensor binary.
//!
//! Without `--kafka` it prints one JSON micro-flow per line on stdout. With
//! `--kafka` (feature `kafka`) flows become v2 `FlowEvent`s that go through
//! the on-disk spool to `traffic.flows.v2`, and sensor counters go to
//! `traffic.health.v2`. Stats are printed on stderr either way.
//!
//!   pi-agents-sensor --leases /data/dnsmasq.leases --pcap capture.pcap
//!   pi-agents-sensor --leases /data/dnsmasq.leases --interface wlan0 \
//!     --kafka pi-agents-kafka-bootstrap:9092 --gateway-id gw-1 --spool-dir /spool

use std::io::{self, Write};
use std::process::ExitCode;

use pi_agents_sensor::attribution::Clients;
use pi_agents_sensor::pcapfile::PcapReader;
use pi_agents_sensor::{FlowRecord, FlowTable, Sensor};

struct Args {
    leases: String,
    pcap: Option<String>,
    interface: Option<String>,
    capacity: usize,
    kafka: Option<String>,
    gateway_id: String,
    spool_dir: String,
    spool_bytes: u64,
    segment_bytes: u64,
    status_file: Option<String>,
}

fn usage() -> ExitCode {
    eprintln!(
        "usage: pi-agents-sensor --leases <dnsmasq.leases> (--pcap <file> | --interface <iface>)\n\
         \x20      [--capacity <flows>] [--kafka <bootstrap> --gateway-id <id> --spool-dir <dir>\n\
         \x20      [--spool-bytes <n>] [--segment-bytes <n>]] [--status-file <path>]"
    );
    ExitCode::from(2)
}

fn parse_args() -> Option<Args> {
    let mut args = Args {
        leases: String::new(),
        pcap: None,
        interface: None,
        capacity: 65_536,
        kafka: None,
        gateway_id: String::new(),
        spool_dir: String::new(),
        spool_bytes: 2 << 30,
        segment_bytes: 64 << 20,
        status_file: None,
    };
    let mut it = std::env::args().skip(1);
    while let Some(flag) = it.next() {
        let value = it.next()?;
        match flag.as_str() {
            "--leases" => args.leases = value,
            "--pcap" => args.pcap = Some(value),
            "--interface" => args.interface = Some(value),
            "--capacity" => args.capacity = value.parse().ok().filter(|c| *c > 0)?,
            "--kafka" => args.kafka = Some(value),
            "--gateway-id" => args.gateway_id = value,
            "--spool-dir" => args.spool_dir = value,
            "--spool-bytes" => args.spool_bytes = value.parse().ok()?,
            "--segment-bytes" => args.segment_bytes = value.parse().ok()?,
            "--status-file" => args.status_file = Some(value),
            _ => return None,
        }
    }
    let one_source = args.pcap.is_some() != args.interface.is_some();
    let kafka_ok = args.kafka.is_none()
        || (!args.gateway_id.is_empty()
            && !args.spool_dir.is_empty()
            && args.spool_bytes >= 2 * args.segment_bytes);
    (!args.leases.is_empty() && one_source && kafka_ok).then_some(args)
}

#[cfg(any(feature = "live", feature = "kafka"))]
fn now_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
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

/// Prints stats on stderr and, if configured, rewrites the status file that
/// the Kubernetes liveness probe checks for freshness.
fn report(sensor: &Sensor, sink: &Sink, status_file: Option<&str>) {
    let s = sensor.stats();
    let line = format!(
        "{{\"frames\":{},\"flows\":{},\"open_flows\":{},\"unattributed\":{},\"non_ip\":{},\"malformed\":{},\"evictions\":{}{}}}",
        s.frames,
        s.flows,
        sensor.open_flows(),
        s.unattributed,
        s.non_ip,
        s.malformed,
        s.evictions,
        sink.stats_json()
    );
    eprintln!("{line}");
    if let Some(path) = status_file {
        let tmp = format!("{path}.tmp");
        let written =
            std::fs::write(&tmp, format!("{line}\n")).and_then(|()| std::fs::rename(&tmp, path));
        if let Err(err) = written {
            eprintln!("warning: cannot write status file {path}: {err}");
        }
    }
}

/// Where flushed micro-flows go.
enum Sink {
    Stdout,
    #[cfg(feature = "kafka")]
    Kafka(Box<kafka_sink::KafkaSink>),
}

impl Sink {
    fn open(args: &Args) -> io::Result<Self> {
        match &args.kafka {
            None => Ok(Sink::Stdout),
            #[cfg(feature = "kafka")]
            Some(bootstrap) => {
                kafka_sink::KafkaSink::open(args, bootstrap).map(|k| Sink::Kafka(Box::new(k)))
            }
            #[cfg(not(feature = "kafka"))]
            Some(_) => Err(io::Error::other(
                "--kafka needs the `kafka` feature (cargo build --features kafka)",
            )),
        }
    }

    fn emit(&mut self, out: &mut Vec<FlowRecord>) -> io::Result<()> {
        match self {
            Sink::Stdout => {
                let stdout = io::stdout();
                let mut lock = stdout.lock();
                for record in out.drain(..) {
                    writeln!(lock, "{}", record.to_json())?;
                }
                Ok(())
            }
            #[cfg(feature = "kafka")]
            Sink::Kafka(k) => k.submit(out),
        }
    }

    /// Frequent work: send spooled events and process delivery reports.
    fn pump(&mut self) -> io::Result<()> {
        match self {
            Sink::Stdout => Ok(()),
            #[cfg(feature = "kafka")]
            Sink::Kafka(k) => k.pump(),
        }
    }

    /// Periodic work: health events for counters that moved, spool fsync.
    fn housekeeping(&mut self, _sensor: &Sensor) -> io::Result<()> {
        match self {
            Sink::Stdout => Ok(()),
            #[cfg(feature = "kafka")]
            Sink::Kafka(k) => k.housekeeping(_sensor),
        }
    }

    /// Delivers what is left before exiting (offline replay).
    fn finish(&mut self) -> io::Result<()> {
        match self {
            Sink::Stdout => Ok(()),
            #[cfg(feature = "kafka")]
            Sink::Kafka(k) => k.finish(),
        }
    }

    fn stats_json(&self) -> String {
        match self {
            Sink::Stdout => String::new(),
            #[cfg(feature = "kafka")]
            Sink::Kafka(k) => k.stats_json(),
        }
    }
}

#[cfg(feature = "kafka")]
mod kafka_sink {
    use std::io;
    use std::path::PathBuf;
    use std::time::{Duration, Instant};

    use pi_agents_sensor::forward::Forwarder;
    use pi_agents_sensor::kafka::KafkaPublisher;
    use pi_agents_sensor::proto::HealthKind;
    use pi_agents_sensor::spool::{Spool, SpoolConfig};
    use pi_agents_sensor::{FlowRecord, Sensor};

    use super::{now_ms, Args};

    pub struct KafkaSink {
        forwarder: Forwarder<KafkaPublisher>,
        reported_unattributed: u64,
        reported_evictions: u64,
        window_start_ms: i64,
    }

    impl KafkaSink {
        pub fn open(args: &Args, bootstrap: &str) -> io::Result<Self> {
            let spool = Spool::open(SpoolConfig {
                dir: PathBuf::from(&args.spool_dir),
                segment_bytes: args.segment_bytes,
                max_bytes: args.spool_bytes,
            })?;
            let publisher = KafkaPublisher::new(bootstrap).map_err(io::Error::other)?;
            Ok(Self {
                forwarder: Forwarder::new(args.gateway_id.clone(), spool, publisher),
                reported_unattributed: 0,
                reported_evictions: 0,
                window_start_ms: now_ms(),
            })
        }

        pub fn submit(&mut self, out: &mut Vec<FlowRecord>) -> io::Result<()> {
            let now = now_ms();
            for record in out.drain(..) {
                self.forwarder.submit_flow(&record, now)?;
            }
            Ok(())
        }

        pub fn pump(&mut self) -> io::Result<()> {
            self.forwarder.pump(5_000)
        }

        pub fn housekeeping(&mut self, sensor: &Sensor) -> io::Result<()> {
            let now = now_ms();
            let stats = sensor.stats();
            let window = (self.window_start_ms, now);
            let unattributed = stats.unattributed - self.reported_unattributed;
            if unattributed > 0 {
                self.forwarder.submit_health(
                    "sensor-attribution",
                    HealthKind::Unattributed,
                    unattributed,
                    (0, 0),
                    window,
                    "frames without a known client MAC",
                    now,
                )?;
                self.reported_unattributed = stats.unattributed;
            }
            let evictions = stats.evictions - self.reported_evictions;
            if evictions > 0 {
                self.forwarder.submit_health(
                    "sensor-flow-table",
                    HealthKind::FlowTableEviction,
                    evictions,
                    (0, 0),
                    window,
                    "flow table full: oldest micro-flows flushed early",
                    now,
                )?;
                self.reported_evictions = stats.evictions;
            }
            self.forwarder.submit_health(
                "sensor",
                HealthKind::Heartbeat,
                stats.frames,
                (0, 0),
                window,
                "",
                now,
            )?;
            self.window_start_ms = now;
            self.forwarder.sync()
        }

        pub fn finish(&mut self) -> io::Result<()> {
            let deadline = Instant::now() + Duration::from_secs(30);
            while self.forwarder.spool().unacked() > 0 && Instant::now() < deadline {
                self.forwarder.pump(10_000)?;
                self.forwarder
                    .publisher_mut()
                    .flush(Duration::from_millis(200));
            }
            self.forwarder.pump(10_000)?;
            self.forwarder.sync()
        }

        pub fn stats_json(&self) -> String {
            let s = self.forwarder.stats();
            format!(
                ",\"sent\":{},\"delivered\":{},\"failed_deliveries\":{},\"lost\":{},\"spooled\":{},\"spool_bytes\":{}",
                s.sent,
                s.delivered,
                s.failed_deliveries,
                s.lost,
                self.forwarder.spool().unacked(),
                self.forwarder.spool().total_bytes()
            )
        }
    }
}

fn replay(path: &str, mut sensor: Sensor, mut sink: Sink) -> io::Result<()> {
    let bytes = std::fs::read(path)?;
    let reader = PcapReader::new(&bytes)
        .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, format!("{err:?}")))?;
    let mut out = Vec::new();
    for record in reader {
        let Ok(record) = record else {
            eprintln!("warning: {path}: truncated record, stopping");
            break;
        };
        sensor.flush_due(record.timestamp_us, &mut out);
        sensor.process_frame(record.timestamp_us, record.data, record.wire_len, &mut out);
        sink.emit(&mut out)?;
        sink.pump()?;
    }
    sensor.flush_all(&mut out);
    sink.emit(&mut out)?;
    sink.housekeeping(&sensor)?;
    sink.finish()?;
    report(&sensor, &sink, None);
    Ok(())
}

#[cfg(feature = "live")]
fn capture(
    interface: &str,
    leases: &str,
    status_file: Option<&str>,
    mut sensor: Sensor,
    mut sink: Sink,
) -> io::Result<()> {
    use std::time::{Duration, Instant};

    let modified = |path: &str| std::fs::metadata(path).and_then(|m| m.modified()).ok();
    let mut capture = pcap::Capture::from_device(interface)
        .and_then(|c| {
            c.snaplen(512)
                .immediate_mode(true)
                .timeout(20)
                .buffer_size(8 << 20)
                .open()
        })
        .map_err(|err| io::Error::other(format!("cannot capture on {interface}: {err}")))?;

    let mut out = Vec::new();
    let mut leases_mtime = modified(leases);
    let mut last_flush = Instant::now();
    let mut last_housekeeping = Instant::now();
    report(&sensor, &sink, status_file);
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
                sensor.flush_all(&mut out);
                sink.emit(&mut out)?;
                sink.housekeeping(&sensor)?;
                return Err(io::Error::other(format!(
                    "capture on {interface} failed: {err}"
                )));
            }
        }
        if last_flush.elapsed() >= Duration::from_millis(20) {
            sensor.flush_due(now_ms() * 1000, &mut out);
            sink.emit(&mut out)?;
            sink.pump()?;
            last_flush = Instant::now();
        }
        if last_housekeeping.elapsed() >= Duration::from_secs(10) {
            let mtime = modified(leases);
            if mtime != leases_mtime {
                sensor.set_clients(load_clients(leases));
                leases_mtime = mtime;
            }
            sink.housekeeping(&sensor)?;
            report(&sensor, &sink, status_file);
            last_housekeeping = Instant::now();
        }
    }
}

#[cfg(not(feature = "live"))]
fn capture(
    _interface: &str,
    _leases: &str,
    _status_file: Option<&str>,
    _sensor: Sensor,
    _sink: Sink,
) -> io::Result<()> {
    Err(io::Error::other(
        "live capture needs the `live` feature (cargo build --features live)",
    ))
}

fn main() -> ExitCode {
    let Some(args) = parse_args() else {
        return usage();
    };
    let sink = match Sink::open(&args) {
        Ok(sink) => sink,
        Err(err) => {
            eprintln!("error: {err}");
            return ExitCode::FAILURE;
        }
    };
    let sensor = Sensor::new(load_clients(&args.leases), FlowTable::new(args.capacity));
    let result = match (&args.pcap, &args.interface) {
        (Some(path), None) => replay(path, sensor, sink),
        (None, Some(interface)) => capture(
            interface,
            &args.leases,
            args.status_file.as_deref(),
            sensor,
            sink,
        ),
        _ => return usage(),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        // A closed stdout (e.g. piped into `head`) is a normal end.
        Err(err) if err.kind() == io::ErrorKind::BrokenPipe => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("error: {err}");
            ExitCode::FAILURE
        }
    }
}
