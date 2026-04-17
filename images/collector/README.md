# collector

`collector` przechwytuje ruch sieciowy z gatewaya i zapisuje go do SQLite. Działa na gateway node i używa:

- `tcpdump` do przechwytywania pakietów
- `tshark` do wyciągania pól z pcap
- SQLite do zapisu `traffic_flows` i aktualizacji `devices`

## Capture Pipeline

Pipeline wygląda tak:

1. `tcpdump` zapisuje krótki pcap do `/tmp`
2. `tshark` czyta pcap i eksportuje wybrane pola
3. parser zamienia rekordy na flow dict
4. collector rozwiązuje tożsamość klienta z DHCP lease
5. collector zapisuje też DNS / ICMP metadata w `traffic_flows.flags`
6. flow trafiają do `traffic_flows`
7. statystyki urządzeń trafiają do `devices.extra_data`

## Protocol Enrichment

Collector wyciąga z `tshark` dodatkowo:

- `dns.flags.rcode`
- `icmp.type`
- `icmp.code`

Pola te są zapisywane jako JSON w `traffic_flows.flags` i później wykorzystywane przez ML/API do `dns_failure_spike`, `icmp_sweep_suspected` i `protocol-signals`.

## Device Identity

Collector nie powinien tworzyć urządzeń z dowolnego MAC/IP. Priorytet jest taki:

1. DHCP lease z `dnsmasq`
2. fallback do poprawnego unicast MAC z flow

Collector odrzuca jako identity:

- `ff:ff:ff:ff:ff:ff`
- multicast MAC
- `0.0.0.0`
- flow bez prywatnego IP z podsieci LAN

## SQLite Optimization

Collector używa SQLite w trybie WAL (Write-Ahead Logging) dla lepszej wydajności read/write:

- `PRAGMA journal_mode=WAL` — writers nie blokują readers
- `PRAGMA synchronous=NORMAL` — balans spójność/wydajność
- `PRAGMA busy_timeout=5000` — retry przy write contention

Indeksy:

- `idx_flows_device_time` — szybsze per-device time-range queries
- `idx_flows_timestamp` — szybsze globalne time-range queries
- `idx_anomaly_device_time` — szybsze per-device anomaly lookups

## Wymagane środowisko

Typowe env:

- `INTERFACE=wlan0`
- `DATABASE_PATH=/data/iot-security.db`
- `BATCH_SIZE=150`
- `CAPTURE_PACKET_COUNT=300`
- `CAPTURE_TIMEOUT=2`
- `LAN_SUBNET_CIDR=192.168.50.0/24`
- `LEASE_FILE_PATH=/gateway-state/dnsmasq.leases`

Metrics endpoint collectora jest wyłączony na MVP.

## Uprawnienia

Collector działa na gateway node z:

- `hostNetwork: true`
- uprawnieniami do sniffingu interfejsu (`privileged` w obecnym MVP)

## Najważniejsze logi

Szukaj sekwencji:

- `starting_tcpdump`
- `tcpdump_finished`
- `pcap_file_stats`
- `starting_tshark`
- `tshark_finished`
- `pcap_processed`
- `flush_started`
- `buffer_flushed`

## Troubleshooting

Jeśli ruch nie trafia do DB:

1. sprawdź logi collectora
2. sprawdź czy `pcap_file_stats.size_bytes > 24`
3. sprawdź czy `tshark_finished.line_count > 0`
4. sprawdź czy `buffer_flushed.inserted > 0`
