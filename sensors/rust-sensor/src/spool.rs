//! Bounded on-disk spool between the sensor and Kafka.
//!
//! Every event is appended to a segmented append-only log before it is sent
//! and removed only after the broker acknowledges it, so a broker outage or a
//! sensor restart does not lose events. Records carry a per-gateway sequence
//! number (`capture_sequence`) that is reserved in blocks in a separate file,
//! so numbers are never reused after a restart. When the spool reaches its
//! byte limit the oldest segment is dropped and reported as a `Loss`.
//!
//! `sync` also records the acknowledgement watermark (every sequence below it
//! is delivered). On restart, records at or above the watermark are sent
//! again: delivery is at-least-once and consumers deduplicate by event ID.
//!
//! Segment record layout (little-endian):
//! `len:u32 crc32:u32 | seq:u64 event_time_ms:i64 topic:u8 key_len:u16 key payload`
//! where `len` and the CRC cover everything after the CRC.

use std::collections::VecDeque;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufReader, Read, Write};
use std::path::{Path, PathBuf};

const SEQUENCE_FILE: &str = "sequence";
const ACKED_FILE: &str = "acked";
const SEGMENT_SUFFIX: &str = ".seg";
const RECORD_HEADER: usize = 8;
const ENTRY_FIXED: usize = 8 + 8 + 1 + 2;
/// Sequence numbers reserved per write of the sequence file.
const SEQUENCE_BLOCK: u64 = 4096;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Topic {
    Flows = 1,
    Health = 2,
}

impl Topic {
    fn from_u8(v: u8) -> Option<Self> {
        match v {
            1 => Some(Topic::Flows),
            2 => Some(Topic::Health),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Entry {
    pub seq: u64,
    pub event_time_ms: i64,
    pub topic: Topic,
    pub key: Vec<u8>,
    pub payload: Vec<u8>,
}

/// Events dropped unacknowledged because the spool was full. The time range
/// covers the dropped segments.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Loss {
    pub count: u64,
    pub first_seq: u64,
    pub last_seq: u64,
    pub first_event_time_ms: i64,
    pub last_event_time_ms: i64,
}

#[derive(Clone, Debug)]
pub struct SpoolConfig {
    pub dir: PathBuf,
    pub segment_bytes: u64,
    pub max_bytes: u64,
}

/// Sequence numbers are contiguous within a segment, so per-record state is
/// one acknowledgement bit; this keeps a 2 GiB spool's index around 2 MB.
struct Segment {
    path: PathBuf,
    first_seq: u64,
    count: u64,
    acked: Vec<u64>,
    unacked: u64,
    min_time: i64,
    max_time: i64,
    bytes: u64,
}

impl Segment {
    fn new(path: PathBuf, first_seq: u64) -> Self {
        Self {
            path,
            first_seq,
            count: 0,
            acked: Vec::new(),
            unacked: 0,
            min_time: i64::MAX,
            max_time: i64::MIN,
            bytes: 0,
        }
    }

    fn contains(&self, seq: u64) -> bool {
        seq >= self.first_seq && seq < self.first_seq + self.count
    }

    fn is_acked(&self, seq: u64) -> bool {
        let i = seq - self.first_seq;
        self.acked[(i / 64) as usize] & (1 << (i % 64)) != 0
    }

    fn set_acked(&mut self, seq: u64) -> bool {
        let i = seq - self.first_seq;
        let word = &mut self.acked[(i / 64) as usize];
        let bit = 1 << (i % 64);
        let newly = *word & bit == 0;
        *word |= bit;
        newly
    }

    fn push(&mut self, event_time_ms: i64, record_len: u64) {
        if self.count.is_multiple_of(64) {
            self.acked.push(0);
        }
        self.count += 1;
        self.unacked += 1;
        self.min_time = self.min_time.min(event_time_ms);
        self.max_time = self.max_time.max(event_time_ms);
        self.bytes += record_len;
    }
}

/// Read position for sending: a segment (by first sequence) and byte offset.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Cursor {
    segment: u64,
    offset: u64,
}

pub struct Spool {
    config: SpoolConfig,
    segments: VecDeque<Segment>,
    writer: Option<File>,
    next_seq: u64,
    reserved_until: u64,
    total_bytes: u64,
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xffff_ffffu32;
    for &byte in data {
        crc ^= u32::from(byte);
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xedb8_8320
            } else {
                crc >> 1
            };
        }
    }
    !crc
}

fn segment_path(dir: &Path, first_seq: u64) -> PathBuf {
    dir.join(format!("{first_seq:020}{SEGMENT_SUFFIX}"))
}

fn encode_entry(entry: &Entry) -> Vec<u8> {
    let mut body = Vec::with_capacity(ENTRY_FIXED + entry.key.len() + entry.payload.len());
    body.extend_from_slice(&entry.seq.to_le_bytes());
    body.extend_from_slice(&entry.event_time_ms.to_le_bytes());
    body.push(entry.topic as u8);
    body.extend_from_slice(&(entry.key.len() as u16).to_le_bytes());
    body.extend_from_slice(&entry.key);
    body.extend_from_slice(&entry.payload);
    let mut record = Vec::with_capacity(RECORD_HEADER + body.len());
    record.extend_from_slice(&(body.len() as u32).to_le_bytes());
    record.extend_from_slice(&crc32(&body).to_le_bytes());
    record.extend_from_slice(&body);
    record
}

fn decode_body(body: &[u8]) -> Option<Entry> {
    let seq = u64::from_le_bytes(body.get(0..8)?.try_into().ok()?);
    let event_time_ms = i64::from_le_bytes(body.get(8..16)?.try_into().ok()?);
    let topic = Topic::from_u8(*body.get(16)?)?;
    let key_len = usize::from(u16::from_le_bytes(body.get(17..19)?.try_into().ok()?));
    let key = body.get(19..19 + key_len)?.to_vec();
    let payload = body.get(19 + key_len..)?.to_vec();
    Some(Entry {
        seq,
        event_time_ms,
        topic,
        key,
        payload,
    })
}

/// Reads valid records from a segment file. Returns the entries and the byte
/// length of the valid prefix; a torn or corrupt tail is ignored.
fn read_segment(path: &Path) -> io::Result<(Vec<Entry>, u64)> {
    let mut reader = BufReader::new(File::open(path)?);
    let mut entries = Vec::new();
    let mut valid = 0u64;
    loop {
        let mut header = [0u8; RECORD_HEADER];
        if reader.read_exact(&mut header).is_err() {
            break;
        }
        let len = u32::from_le_bytes(header[0..4].try_into().unwrap()) as usize;
        let crc = u32::from_le_bytes(header[4..8].try_into().unwrap());
        let mut body = vec![0u8; len];
        if reader.read_exact(&mut body).is_err() || crc32(&body) != crc {
            break;
        }
        match decode_body(&body) {
            Some(entry) => entries.push(entry),
            None => break,
        }
        valid += (RECORD_HEADER + len) as u64;
    }
    Ok((entries, valid))
}

impl Spool {
    /// Opens (or creates) the spool, recovering segments left by a previous
    /// run. A torn record at the end of the last segment is truncated.
    pub fn open(config: SpoolConfig) -> io::Result<Self> {
        assert!(
            config.max_bytes >= 2 * config.segment_bytes,
            "spool must hold at least two segments"
        );
        fs::create_dir_all(&config.dir)?;
        let mut paths: Vec<PathBuf> = fs::read_dir(&config.dir)?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.to_string_lossy().ends_with(SEGMENT_SUFFIX))
            .collect();
        paths.sort();

        let mut segments = VecDeque::new();
        let mut total_bytes = 0;
        let mut max_seq = None;
        for path in paths {
            let (entries, valid) = read_segment(&path)?;
            if entries.is_empty() {
                fs::remove_file(&path)?;
                continue;
            }
            if valid < fs::metadata(&path)?.len() {
                OpenOptions::new().write(true).open(&path)?.set_len(valid)?;
            }
            max_seq = entries.last().map(|e| e.seq).max(max_seq);
            let mut segment = Segment::new(path, entries[0].seq);
            for entry in &entries {
                segment.push(entry.event_time_ms, 0);
            }
            segment.bytes = valid;
            segments.push_back(segment);
            total_bytes += valid;
        }

        let reserved = fs::read_to_string(config.dir.join(SEQUENCE_FILE))
            .ok()
            .and_then(|s| s.trim().parse::<u64>().ok())
            .unwrap_or(0);
        let next_seq = reserved.max(max_seq.map_or(0, |s| s + 1));
        let mut spool = Self {
            config,
            segments,
            writer: None,
            next_seq,
            reserved_until: next_seq,
            total_bytes,
        };
        spool.reserve_sequences()?;

        let watermark = fs::read_to_string(spool.config.dir.join(ACKED_FILE))
            .ok()
            .and_then(|s| s.trim().parse::<u64>().ok())
            .unwrap_or(0);
        for segment in spool.segments.iter_mut() {
            let end = (segment.first_seq + segment.count).min(watermark);
            for seq in segment.first_seq..end {
                if segment.set_acked(seq) {
                    segment.unacked -= 1;
                }
            }
        }
        spool.drop_acked_front()?;
        Ok(spool)
    }

    fn reserve_sequences(&mut self) -> io::Result<()> {
        let until = self.next_seq + SEQUENCE_BLOCK;
        let tmp = self.config.dir.join(format!("{SEQUENCE_FILE}.tmp"));
        {
            let mut file = File::create(&tmp)?;
            writeln!(file, "{until}")?;
            file.sync_all()?;
        }
        fs::rename(&tmp, self.config.dir.join(SEQUENCE_FILE))?;
        self.reserved_until = until;
        Ok(())
    }

    /// The sequence number the next `append` will use.
    pub fn next_seq(&self) -> u64 {
        self.next_seq
    }

    pub fn total_bytes(&self) -> u64 {
        self.total_bytes
    }

    pub fn unacked(&self) -> u64 {
        self.segments.iter().map(|s| s.unacked).sum()
    }

    /// Appends an event whose payload is built from its sequence number.
    /// Returns the sequence and any loss caused by dropping old segments.
    pub fn append(
        &mut self,
        topic: Topic,
        key: &[u8],
        event_time_ms: i64,
        payload: impl FnOnce(u64) -> Vec<u8>,
    ) -> io::Result<(u64, Option<Loss>)> {
        if self.next_seq >= self.reserved_until {
            self.reserve_sequences()?;
        }
        let seq = self.next_seq;
        let entry = Entry {
            seq,
            event_time_ms,
            topic,
            key: key.to_vec(),
            payload: payload(seq),
        };
        let record = encode_entry(&entry);
        let record_len = record.len() as u64;

        let rotate = match self.segments.back() {
            Some(last) => {
                self.writer.is_none() || last.bytes + record_len > self.config.segment_bytes
            }
            None => true,
        };
        if rotate {
            self.rotate(seq)?;
        }
        let loss = self.enforce_limit(record_len)?;

        let writer = self.writer.as_mut().expect("active segment after rotate");
        writer.write_all(&record)?;
        let segment = self.segments.back_mut().expect("active segment");
        segment.push(event_time_ms, record_len);
        self.total_bytes += record_len;
        self.next_seq += 1;
        Ok((seq, loss))
    }

    fn rotate(&mut self, first_seq: u64) -> io::Result<()> {
        if let Some(writer) = self.writer.take() {
            writer.sync_data()?;
        }
        let path = segment_path(&self.config.dir, first_seq);
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        self.segments.push_back(Segment::new(path, first_seq));
        self.writer = Some(file);
        Ok(())
    }

    /// Drops the oldest closed segments until `incoming` bytes fit.
    fn enforce_limit(&mut self, incoming: u64) -> io::Result<Option<Loss>> {
        let mut loss: Option<Loss> = None;
        while self.total_bytes + incoming > self.config.max_bytes && self.segments.len() > 1 {
            let segment = self.segments.pop_front().expect("len > 1");
            fs::remove_file(&segment.path)?;
            self.total_bytes -= segment.bytes;
            if segment.unacked == 0 {
                continue;
            }
            let mut unacked = (segment.first_seq..segment.first_seq + segment.count)
                .filter(|&seq| !segment.is_acked(seq));
            let (first, last) = (unacked.clone().next(), unacked.next_back());
            let (Some(first), Some(last)) = (first, last) else {
                continue;
            };
            let l = loss.get_or_insert(Loss {
                count: 0,
                first_seq: first,
                last_seq: last,
                first_event_time_ms: segment.min_time,
                last_event_time_ms: segment.max_time,
            });
            l.count += segment.unacked;
            l.last_seq = last;
            l.first_event_time_ms = l.first_event_time_ms.min(segment.min_time);
            l.last_event_time_ms = l.last_event_time_ms.max(segment.max_time);
        }
        Ok(loss)
    }

    /// Marks a sequence as delivered. Fully acknowledged closed segments are
    /// deleted. Unknown or repeated sequences are ignored.
    pub fn ack(&mut self, seq: u64) -> io::Result<()> {
        let Some(segment) = self.segments.iter_mut().find(|s| s.contains(seq)) else {
            return Ok(());
        };
        if segment.set_acked(seq) {
            segment.unacked -= 1;
        }
        self.drop_acked_front()
    }

    /// Removes fully acknowledged segments from the front, except the last
    /// one (which may still be written to).
    fn drop_acked_front(&mut self) -> io::Result<()> {
        while self.segments.len() > 1 && self.segments[0].unacked == 0 {
            let segment = self.segments.pop_front().expect("len > 1");
            fs::remove_file(&segment.path)?;
            self.total_bytes -= segment.bytes;
        }
        Ok(())
    }

    /// The lowest sequence not yet acknowledged (`next_seq` when none).
    pub fn ack_watermark(&self) -> u64 {
        for segment in &self.segments {
            if segment.unacked > 0 {
                return (segment.first_seq..segment.first_seq + segment.count)
                    .find(|&seq| !segment.is_acked(seq))
                    .expect("unacked > 0");
            }
        }
        self.next_seq
    }

    /// Flushes the active segment to disk.
    pub fn sync(&mut self) -> io::Result<()> {
        if let Some(writer) = &self.writer {
            writer.sync_data()?;
        }
        let tmp = self.config.dir.join(format!("{ACKED_FILE}.tmp"));
        fs::write(&tmp, format!("{}\n", self.ack_watermark()))?;
        fs::rename(&tmp, self.config.dir.join(ACKED_FILE))
    }

    /// A cursor at the oldest segment that still has unacknowledged entries,
    /// i.e. where sending must resume after a restart or failed delivery.
    pub fn rewind(&self) -> Cursor {
        self.segments
            .iter()
            .find(|s| s.unacked > 0)
            .or(self.segments.back())
            .map(|s| Cursor {
                segment: s.first_seq,
                offset: 0,
            })
            .unwrap_or_default()
    }

    /// Reads up to `limit` unacknowledged entries at or after `cursor`, in
    /// sequence order, each with its own position, and advances the cursor
    /// past them. Acknowledged entries are skipped.
    pub fn read(&mut self, cursor: &mut Cursor, limit: usize) -> io::Result<Vec<(Cursor, Entry)>> {
        if let Some(writer) = self.writer.as_mut() {
            writer.flush()?;
        }
        let mut out = Vec::new();
        let mut index = match self
            .segments
            .iter()
            .position(|s| s.first_seq == cursor.segment)
        {
            Some(index) => index,
            // The segment was dropped or deleted: continue at the oldest one.
            None => {
                *cursor = Cursor {
                    segment: self.segments.front().map_or(0, |s| s.first_seq),
                    offset: 0,
                };
                0
            }
        };
        while out.len() < limit && index < self.segments.len() {
            let segment = &self.segments[index];
            let mut reader = BufReader::new(File::open(&segment.path)?);
            io::copy(&mut (&mut reader).take(cursor.offset), &mut io::sink())?;
            while out.len() < limit {
                let position = *cursor;
                let mut header = [0u8; RECORD_HEADER];
                if reader.read_exact(&mut header).is_err() {
                    break;
                }
                let len = u32::from_le_bytes(header[0..4].try_into().unwrap()) as usize;
                let mut body = vec![0u8; len];
                if reader.read_exact(&mut body).is_err() {
                    break;
                }
                cursor.offset += (RECORD_HEADER + len) as u64;
                if let Some(entry) = decode_body(&body) {
                    if segment.contains(entry.seq) && !segment.is_acked(entry.seq) {
                        out.push((position, entry));
                    }
                }
            }
            let at_end = cursor.offset >= segment.bytes;
            if !at_end || index + 1 == self.segments.len() {
                break;
            }
            index += 1;
            *cursor = Cursor {
                segment: self.segments[index].first_seq,
                offset: 0,
            };
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(name: &str) -> Self {
            let dir = std::env::temp_dir().join(format!(
                "pi-agents-spool-{name}-{}-{:?}",
                std::process::id(),
                std::time::SystemTime::now()
            ));
            fs::create_dir_all(&dir).unwrap();
            Self(dir)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn config(dir: &TempDir, segment_bytes: u64, max_bytes: u64) -> SpoolConfig {
        SpoolConfig {
            dir: dir.0.clone(),
            segment_bytes,
            max_bytes,
        }
    }

    fn append(spool: &mut Spool, time: i64) -> (u64, Option<Loss>) {
        spool
            .append(Topic::Flows, b"gw:dev", time, |seq| {
                seq.to_le_bytes().repeat(8)
            })
            .unwrap()
    }

    #[test]
    fn acknowledged_segments_are_deleted_and_unacked_ones_are_readable() {
        let dir = TempDir::new("ack");
        let mut spool = Spool::open(config(&dir, 400, 4000)).unwrap();
        let seqs: Vec<u64> = (0..10).map(|t| append(&mut spool, t).0).collect();
        assert_eq!(seqs, (0..10).collect::<Vec<_>>());
        assert_eq!(spool.unacked(), 10);

        for seq in 0..6 {
            spool.ack(seq).unwrap();
        }
        let pending = spool.read(&mut spool.rewind(), 100).unwrap();
        assert_eq!(
            pending.iter().map(|e| e.1.seq).collect::<Vec<_>>(),
            vec![6, 7, 8, 9]
        );
        assert_eq!(pending[0].1.payload, 6u64.to_le_bytes().repeat(8));
        assert!(spool.total_bytes() < 10 * 100);
    }

    #[test]
    fn restart_resends_unacked_entries_and_never_reuses_sequences() {
        let dir = TempDir::new("restart");
        {
            let mut spool = Spool::open(config(&dir, 400, 4000)).unwrap();
            for t in 0..5 {
                append(&mut spool, t);
            }
            spool.ack(0).unwrap();
            // Acked out of order: not below the watermark, so resent.
            spool.ack(2).unwrap();
            spool.sync().unwrap();
            assert_eq!(spool.ack_watermark(), 1);
        }
        let mut spool = Spool::open(config(&dir, 400, 4000)).unwrap();
        let pending: Vec<u64> = spool
            .read(&mut spool.rewind(), 100)
            .unwrap()
            .iter()
            .map(|e| e.1.seq)
            .collect();
        // The synced watermark (1) survives; everything after it is resent.
        assert_eq!(pending, vec![1, 2, 3, 4]);
        assert_eq!(spool.unacked(), 4);
        let (seq, _) = append(&mut spool, 9);
        assert!(seq >= SEQUENCE_BLOCK, "sequence {seq} reused after restart");
    }

    #[test]
    fn torn_tail_is_truncated_on_recovery() {
        let dir = TempDir::new("torn");
        let path;
        {
            let mut spool = Spool::open(config(&dir, 4000, 8000)).unwrap();
            append(&mut spool, 1);
            append(&mut spool, 2);
            spool.sync().unwrap();
            path = spool.segments[0].path.clone();
        }
        let mut file = OpenOptions::new().append(true).open(&path).unwrap();
        file.write_all(&[42, 0, 0, 0, 1, 2]).unwrap();
        drop(file);

        let mut spool = Spool::open(config(&dir, 4000, 8000)).unwrap();
        assert_eq!(spool.read(&mut spool.rewind(), 10).unwrap().len(), 2);
        append(&mut spool, 3);
        assert_eq!(spool.read(&mut spool.rewind(), 10).unwrap().len(), 3);
    }

    #[test]
    fn full_spool_drops_the_oldest_segment_and_reports_the_loss() {
        let dir = TempDir::new("full");
        let mut spool = Spool::open(config(&dir, 300, 600)).unwrap();
        let mut losses = Vec::new();
        for t in 0..20 {
            if let (_, Some(loss)) = append(&mut spool, 1000 + t) {
                losses.push(loss);
            }
        }
        assert!(!losses.is_empty());
        let first = &losses[0];
        assert_eq!(first.first_seq, 0);
        assert_eq!(first.count, first.last_seq - first.first_seq + 1);
        assert_eq!(first.first_event_time_ms, 1000);
        assert!(spool.total_bytes() <= 600);

        let lost: u64 = losses.iter().map(|l| l.count).sum();
        assert_eq!(lost + spool.unacked(), 20);
    }
}
