//! Reader for classic libpcap files with Ethernet link type. Used for fixture
//! tests and offline replay; live capture uses libpcap behind the `live`
//! feature.

#[derive(Debug, Eq, PartialEq)]
pub enum PcapError {
    BadMagic,
    UnsupportedLinkType(u32),
    Truncated,
}

pub struct PcapRecord<'a> {
    pub timestamp_us: i64,
    pub wire_len: u32,
    pub data: &'a [u8],
}

pub struct PcapReader<'a> {
    bytes: &'a [u8],
    at: usize,
    big_endian: bool,
    nanos: bool,
}

const LINKTYPE_ETHERNET: u32 = 1;

impl<'a> PcapReader<'a> {
    pub fn new(bytes: &'a [u8]) -> Result<Self, PcapError> {
        let magic = bytes.get(0..4).ok_or(PcapError::Truncated)?;
        let (big_endian, nanos) = match magic {
            [0xd4, 0xc3, 0xb2, 0xa1] => (false, false),
            [0xa1, 0xb2, 0xc3, 0xd4] => (true, false),
            [0x4d, 0x3c, 0xb2, 0xa1] => (false, true),
            [0xa1, 0xb2, 0x3c, 0x4d] => (true, true),
            _ => return Err(PcapError::BadMagic),
        };
        let mut reader = Self {
            bytes,
            at: 24,
            big_endian,
            nanos,
        };
        let link_type = reader.u32_at(20).ok_or(PcapError::Truncated)?;
        if link_type != LINKTYPE_ETHERNET {
            return Err(PcapError::UnsupportedLinkType(link_type));
        }
        reader.at = 24;
        Ok(reader)
    }

    fn u32_at(&self, at: usize) -> Option<u32> {
        let b: [u8; 4] = self.bytes.get(at..at + 4)?.try_into().ok()?;
        Some(if self.big_endian {
            u32::from_be_bytes(b)
        } else {
            u32::from_le_bytes(b)
        })
    }
}

impl<'a> Iterator for PcapReader<'a> {
    type Item = Result<PcapRecord<'a>, PcapError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.at >= self.bytes.len() {
            return None;
        }
        let header = (
            self.u32_at(self.at),
            self.u32_at(self.at + 4),
            self.u32_at(self.at + 8),
            self.u32_at(self.at + 12),
        );
        let (Some(secs), Some(frac), Some(captured), Some(wire_len)) = header else {
            self.at = self.bytes.len();
            return Some(Err(PcapError::Truncated));
        };
        let start = self.at + 16;
        let Some(data) = self.bytes.get(start..start + captured as usize) else {
            self.at = self.bytes.len();
            return Some(Err(PcapError::Truncated));
        };
        self.at = start + captured as usize;
        let micros = if self.nanos {
            i64::from(frac) / 1000
        } else {
            i64::from(frac)
        };
        Some(Ok(PcapRecord {
            timestamp_us: i64::from(secs) * 1_000_000 + micros,
            wire_len,
            data,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_unknown_magic_and_link_type() {
        assert!(matches!(
            PcapReader::new(&[0u8; 24]),
            Err(PcapError::BadMagic)
        ));
        let mut header = vec![0xd4, 0xc3, 0xb2, 0xa1];
        header.extend_from_slice(&[0u8; 16]);
        header.extend_from_slice(&105u32.to_le_bytes());
        assert!(matches!(
            PcapReader::new(&header),
            Err(PcapError::UnsupportedLinkType(105))
        ));
    }

    #[test]
    fn reports_truncated_records() {
        let mut file = vec![0xd4, 0xc3, 0xb2, 0xa1];
        file.extend_from_slice(&[0u8; 16]);
        file.extend_from_slice(&1u32.to_le_bytes());
        file.extend_from_slice(&[1, 0, 0, 0, 0, 0, 0, 0, 100, 0, 0, 0, 100, 0, 0, 0]);
        file.extend_from_slice(&[0u8; 10]);
        let mut reader = PcapReader::new(&file).expect("valid header");
        assert!(matches!(reader.next(), Some(Err(PcapError::Truncated))));
        assert!(reader.next().is_none());
    }
}
