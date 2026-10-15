//! Device attribution by client MAC. The gateway is the Wi-Fi AP, so a frame
//! from a client carries the client's MAC as source and a frame to it carries
//! it as destination. Known clients come from the dnsmasq lease file written
//! by `gateway-agent`.

use std::collections::HashSet;

use crate::packet::{parse_mac, Mac};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Attribution {
    /// The client sent this frame.
    FromDevice(Mac),
    /// The frame is addressed to the client.
    ToDevice(Mac),
    Unattributed,
}

#[derive(Clone, Debug, Default)]
pub struct Clients {
    macs: HashSet<Mac>,
}

impl Clients {
    pub fn from_macs(macs: impl IntoIterator<Item = Mac>) -> Self {
        Self {
            macs: macs.into_iter().collect(),
        }
    }

    /// Parses dnsmasq's lease file (`<expiry> <mac> <ip> <hostname> <client-id>`
    /// per line). Malformed lines are skipped.
    pub fn from_dnsmasq_leases(contents: &str) -> Self {
        Self::from_macs(
            contents
                .lines()
                .filter_map(|line| line.split_whitespace().nth(1))
                .filter_map(parse_mac),
        )
    }

    pub fn len(&self) -> usize {
        self.macs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.macs.is_empty()
    }

    pub fn attribute(&self, src_mac: &Mac, dst_mac: &Mac) -> Attribution {
        if self.macs.contains(src_mac) {
            Attribution::FromDevice(*src_mac)
        } else if self.macs.contains(dst_mac) {
            Attribution::ToDevice(*dst_mac)
        } else {
            Attribution::Unattributed
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: Mac = [0x02, 0, 0, 0, 0, 0x0a];
    const B: Mac = [0x02, 0, 0, 0, 0, 0x0b];
    const GW: Mac = [0x02, 0, 0, 0, 0, 0x01];

    #[test]
    fn parses_dnsmasq_leases_and_skips_garbage() {
        let clients = Clients::from_dnsmasq_leases(
            "1760000000 02:00:00:00:00:0a 192.168.50.10 sensor-a *\n\
             garbage\n\
             1760000000 not-a-mac 192.168.50.11 x *\n\
             1760000000 02:00:00:00:00:0B 192.168.50.12 * 01:02\n",
        );
        assert_eq!(clients.len(), 2);
        assert_eq!(clients.attribute(&B, &GW), Attribution::FromDevice(B));
    }

    #[test]
    fn attributes_by_source_then_destination() {
        let clients = Clients::from_macs([A]);
        assert_eq!(clients.attribute(&A, &GW), Attribution::FromDevice(A));
        assert_eq!(clients.attribute(&GW, &A), Attribution::ToDevice(A));
        assert_eq!(clients.attribute(&GW, &B), Attribution::Unattributed);
    }
}
