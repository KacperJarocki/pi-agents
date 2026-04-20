# Collector — Zbieranie ruchu sieciowego

> Ostatnia aktualizacja: Kwiecień 2026 (Fazy 7–11)

---

## Spis treści

1. [Co robi collector?](#co-robi-collector)
2. [Pipeline przechwytywania](#pipeline-przechwytywania)
3. [Pola tshark](#pola-tshark)
4. [Rozwiązywanie tożsamości urządzenia](#rozwiązywanie-tożsamości-urządzenia)
5. [Buforowanie i zapis do bazy](#buforowanie-i-zapis-do-bazy)
6. [Akumulacja extra_data](#akumulacja-extra_data)
7. [Tabele w bazie danych](#tabele-w-bazie-danych)
8. [Konfiguracja](#konfiguracja)
9. [Wdrożenie K8s](#wdrożenie-k8s)

---

## Co robi collector?

Collector to komponent odpowiedzialny za **przechwytywanie surowego ruchu sieciowego** z interfejsu WiFi AP
i zapisywanie go do bazy SQLite jako ustrukturyzowane rekordy `traffic_flows`. Jest to pierwszy krok
w pipeline danych — bez collectora system nie ma żadnych danych do analizy.

Collector działa jako ciągła pętla: przechwytuje pakiety → parsuje je → identyfikuje urządzenia →
buforuje → zapisuje batchami do bazy.

---

## Pipeline przechwytywania

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Pętla główna                                 │
│                                                                     │
│   tcpdump -i wlan0 -c 300 -w /tmp/capture.pcap                     │
│        │                                                            │
│        ▼                                                            │
│   tshark -r capture.pcap -T fields -E separator='|'                 │
│     -e ip.src -e ip.dst -e tcp.srcport ... (15 pól)                 │
│        │                                                            │
│        ▼                                                            │
│   parse_tshark_line() → dict z polami                               │
│        │                                                            │
│        ▼                                                            │
│   resolve_device() → device_id z bazy                               │
│        │                                                            │
│        ▼                                                            │
│   bufor (list) → flush co FLUSH_INTERVAL lub BATCH_SIZE             │
│        │                                                            │
│        ▼                                                            │
│   INSERT traffic_flows + UPDATE devices.extra_data                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Kroki szczegółowo

1. **tcpdump** — przechwytuje `CAPTURE_PACKET_COUNT` pakietów (domyślnie 300 w K8s) z interfejsu `INTERFACE` (domyślnie `wlan0`), zapisuje do pliku pcap w `/tmp`. Parametr `SNAPLEN=128` ogranicza rozmiar przechwyconych danych na pakiet.

2. **tshark** — czyta plik pcap i wypisuje 15 pól oddzielonych `|` (pipe). Każda linia = jeden pakiet.

3. **parse_tshark_line()** — parsuje linię tshark na dict. Obsługuje brakujące pola, konwertuje porty TCP/UDP, łączy DNS query i flagi ICMP do dodatkowych pól.

4. **resolve_device()** — identyfikuje urządzenie na podstawie IP/MAC (szczegóły niżej).

5. **Buforowanie** — sparsowane flow-y trafiają do bufora w pamięci. Flush następuje gdy:
   - Bufor osiągnie `BATCH_SIZE` (150)
   - Minie `FLUSH_INTERVAL` sekund (2s)
   - Bufor osiągnie `MAX_BUFFER_SIZE` (600) — emergency flush

6. **Zapis** — batch INSERT do `traffic_flows` + UPDATE `devices.extra_data` z zagregowanymi statystykami.

### Obsługa błędów

- Jeśli flush do bazy się nie powiedzie, flow-y wracają na początek bufora (prepend)
- Między cyklami tcpdump przy błędzie jest 1s sleep
- Proces tcpdump/tshark ma timeout = `CAPTURE_TIMEOUT` sekund
- Przy zamknięciu (SIGTERM/SIGINT), procesy potomne są terminowane z 5s timeoutem

---

## Pola tshark

Collector przechwytuje 15 pól z każdego pakietu. Nie wszystkie są aktywnie używane — niektóre służą jako fallback.

| # | Pole tshark | Używane? | Cel |
|---|-------------|----------|-----|
| 0 | `ip.src` | ✅ | Źródłowy IP — do identyfikacji urządzenia i `src_ip` w bazie |
| 1 | `ip.dst` | ✅ | Docelowy IP — `dst_ip` w bazie |
| 2 | `tcp.srcport` | ✅ | Port źródłowy TCP |
| 3 | `tcp.dstport` | ✅ | Port docelowy TCP |
| 4 | `udp.srcport` | ✅ | Port źródłowy UDP (fallback jeśli TCP pusty) |
| 5 | `udp.dstport` | ✅ | Port docelowy UDP (fallback jeśli TCP pusty) |
| 6 | `_ws.col.Protocol` | ✅ | Nazwa protokołu (TCP, UDP, DNS, ICMP...) |
| 7 | `frame.len` | ✅ | Rozmiar ramki → `bytes_sent` (outbound) lub `bytes_received` (inbound) |
| 8 | `tcp.len` | ❌ | Przechwycone, ale nieużywane |
| 9 | `udp.length` | ❌ | Przechwycone, ale nieużywane |
| 10 | `eth.addr` | ✅ | Adresy MAC (src + dst) — do identyfikacji urządzenia |
| 11 | `dns.qry.name` | ✅ | Nazwa zapytania DNS → `dns_query` |
| 12 | `dns.flags.rcode` | ✅ | Kod odpowiedzi DNS → `flags.dns_rcode` |
| 13 | `icmp.type` | ✅ | Typ ICMP → `flags.icmp_type` |
| 14 | `icmp.code` | ✅ | Kod ICMP → `flags.icmp_code` |

**Uwaga**: `eth.addr` zwraca oba adresy MAC w jednym polu (rozdzielone przecinkiem), np. `aa:bb:cc:dd:ee:ff,11:22:33:44:55:66`. Collector parsuje oba i wybiera odpowiedni w zależności od kierunku ruchu.

---

## Rozwiązywanie tożsamości urządzenia

Collector musi przypisać każdy pakiet do konkretnego urządzenia (rekord `devices` w bazie). To nietrywialny problem, bo:
- Pakiety mają IP, ale urządzenia mogą zmieniać IP (DHCP)
- MAC jest stabilniejszy, ale nie zawsze dostępny
- `eth.addr` zawiera dwa adresy MAC — trzeba wybrać właściwy

### Łańcuch identyfikacji

```
1. Szukaj DHCP lease po IP źródłowym (src_ip)
   ├── Znaleziony → użyj MAC z lease → device = mac_address
   │   └── Cross-validation: sprawdź eth.addr z pakietu
   │       ├── eth.src == lease MAC → OK
   │       └── eth.src != lease MAC → użyj lease MAC (zaufany)
   │
   └── Nie znaleziony → szukaj DHCP lease po IP docelowym (dst_ip)
       ├── Znaleziony → ruch przychodzący, użyj MAC z lease
       │   └── eth.dst powinien pasować
       │
       └── Nie znaleziony nigdzie
           └── Direction-aware MAC selection
               ├── src_ip w LAN_SUBNET → urządzenie lokalne → eth.src
               ├── dst_ip w LAN_SUBNET → urządzenie lokalne → eth.dst
               └── Żaden w LAN → placeholder "ip:<src_ip>"
```

### Cache DHCP lease

Collector trzyma w pamięci cache leases z pliku dnsmasq (`LEASE_FILE_PATH`). Cache ma TTL = **10 sekund**
i jest aktualizowany automatycznie.

Dwa słowniki cache:
- `by_ip` — klucz = IP, wartość = `(mac, hostname)`
- `by_mac` — klucz = MAC, wartość = `(ip, hostname)` (do cross-validation)

### Dlaczego direction-aware?

W normalnym WiFi AP, `eth.src` to MAC urządzenia które **wysyła** pakiet. Ale collector widzi
ruch w obu kierunkach (bo nasłuchuje na interfejsie AP). Dla ruchu przychodzącego na urządzenie
IoT, `eth.src` to MAC routera/gatewaya, a `eth.dst` to MAC urządzenia.

Dlatego collector sprawdza, czy IP jest w `LAN_SUBNET_CIDR`:
- Jeśli `src_ip` jest w LAN → to urządzenie lokalne wysyła → użyj `eth.src` (i `frame.len` → `bytes_sent`)
- Jeśli `dst_ip` jest w LAN → to ruch przychodzi do urządzenia → użyj `eth.dst` (i `frame.len` → `bytes_received`)

Ta sama logika kierunkowości (od Fazy 11) jest używana do wypełnienia `bytes_sent`/`bytes_received`
w rekordzie `traffic_flows`. Pozwala ML pipeline rozróżnić exfiltrację danych (wysokie `bytes_sent`)
od pobierania (wysokie `bytes_received`).

---

## Buforowanie i zapis do bazy

### Parametry buforowania

| Parametr | Domyślny | K8s | Opis |
|----------|----------|-----|------|
| `BATCH_SIZE` | 150 | 150 | Ilość flow-ów do zgromadzenia przed flush |
| `FLUSH_INTERVAL` | 2s | 2s | Maksymalny czas oczekiwania na flush |
| `MAX_BUFFER_SIZE` | 600 | 600 | Limit bufora w pamięci (4 × BATCH_SIZE) |

### Proces flush

1. Collector sprawdza co `FLUSH_INTERVAL` sekund, czy bufor ma flow-y
2. Jeśli `len(buffer) >= BATCH_SIZE` — flush natychmiast
3. Jeśli `len(buffer) >= MAX_BUFFER_SIZE` — emergency flush (loguje warning)
4. Flush = jedna transakcja SQLite:
   - `INSERT INTO traffic_flows` — batch insert wszystkich flow-ów
   - `UPDATE devices SET extra_data = ...` — zagregowane statystyki per urządzenie
5. Po udanym flush, bufor jest czyszczony
6. Przy błędzie flush, flow-y wracają na początek bufora (retry w następnym cyklu)

---

## Akumulacja extra_data

Pole `devices.extra_data` (JSON) przechowuje zagregowane statystyki urządzenia. Część pól jest **kumulatywna** (rośnie z każdym flush), a część jest **nadpisywana** (odzwierciedla ostatni batch).

| Pole | Typ akumulacji | Opis |
|------|----------------|------|
| `total_bytes` | ✅ Kumulatywne | Suma `bytes_sent` ze wszystkich flow-ów urządzenia |
| `packet_count` | ✅ Kumulatywne | Suma pakietów |
| `unique_connections` | ❌ Nadpisywane | Unikalne pary (dst_ip, dst_port) z ostatniego batcha |
| `unique_destinations` | ❌ Nadpisywane | Unikalne dst_ip z ostatniego batcha |
| `ports` | ❌ Nadpisywane | Lista do 10 unikalnych portów z ostatniego batcha |

### Dlaczego tak?

`total_bytes` i `packet_count` muszą być kumulatywne, bo dają obraz całkowitego ruchu od pierwszego
pojawienia się urządzenia. Reszta daje obraz "co robi urządzenie teraz" — bardziej przydatne
do szybkiego podglądu niż suma historyczna.

---

## Tabele w bazie danych

Collector operuje na dwóch tabelach:

### traffic_flows (INSERT)

Każdy rekord = jeden sparsowany pakiet z tshark.

| Kolumna | Wartość z collectora |
|---------|---------------------|
| `device_id` | FK do `devices.id` (z resolve_device) |
| `timestamp` | Czas przechwycenia pakietu (czas captury, nie czas flushu) |
| `src_ip` | `ip.src` z tshark |
| `dst_ip` | `ip.dst` z tshark |
| `src_port` | TCP/UDP port źródłowy (0 dla ICMP) |
| `dst_port` | TCP/UDP port docelowy (0 dla ICMP) |
| `protocol` | `_ws.col.Protocol` |
| `bytes_sent` | `frame.len` gdy `src_ip` jest w LAN (urządzenie wysyła, outbound) |
| `bytes_received` | `frame.len` gdy `dst_ip` jest w LAN (urządzenie odbiera, inbound); od Fazy 11 |
| `packets` | Zawsze 1 (jeden rekord = jeden pakiet) |
| `duration_ms` | Zawsze 0 |
| `dns_query` | `dns.qry.name` (jeśli to pakiet DNS) |
| `flags` | JSON: `{dns_rcode, icmp_type, icmp_code}` |

### devices (INSERT/UPDATE)

- **INSERT**: `get_or_create_device()` — tworzy rekord jeśli nie istnieje (po `mac_address`)
- **UPDATE**: `last_seen`, `ip_address`, `hostname`, `is_active=1`, `extra_data` (zagregowane statystyki)

---

## Konfiguracja

### Zmienne środowiskowe

| Zmienna | Domyślna | Opis |
|---------|----------|------|
| `DATABASE_PATH` | `/data/iot-security.db` | Ścieżka do bazy SQLite |
| `INTERFACE` | `wlan0` | Interfejs sieciowy do nasłuchiwania |
| `BATCH_SIZE` | `150` | Rozmiar batcha (flush po tylu flow-ach) |
| `FLUSH_INTERVAL` | `2` | Interwał flush w sekundach |
| `CAPTURE_PACKET_COUNT` | = BATCH_SIZE | Ile pakietów przechwycić w jednym cyklu tcpdump |
| `CAPTURE_TIMEOUT` | = FLUSH_INTERVAL | Timeout tcpdump w sekundach |
| `SNAPLEN` | `128` | Maksymalny rozmiar przechwyconego pakietu (bajtów) |
| `MAX_BUFFER_SIZE` | BATCH_SIZE × 4 | Maksymalny rozmiar bufora w pamięci |
| `LAN_SUBNET_CIDR` | `192.168.50.0/24` | Podsieć LAN (do direction-aware MAC) |
| `LEASE_FILE_PATH` | `/gateway-state/dnsmasq.leases` | Ścieżka do pliku leases dnsmasq |
| `ENABLE_METRICS` | `false` | Włącz endpoint Prometheus (wyłączony w MVP) |
| `METRICS_PORT` | `9090` | Port metryk Prometheus |

### Stałe w kodzie

| Stała | Wartość | Opis |
|-------|---------|------|
| `_lease_cache_ttl` | 10.0s | TTL cache DHCP leases |
| `busy_timeout` | 5000ms | SQLite busy timeout |
| Terminate timeout | 5s | Czas na zamknięcie procesów potomnych |

---

## Wdrożenie K8s

### Wymagania

Collector **musi** mieć:
- `hostNetwork: true` — bezpośredni dostęp do interfejsu sieciowego hosta
- `dnsPolicy: ClusterFirstWithHostNet` — DNS klastra mimo hostNetwork
- `privileged: true` — wymagane przez tcpdump/tshark do raw capture
- `runAsUser: 0` — root (wymagane przez narzędzia przechwytywania)
- `nodeSelector: node-role.kubernetes.io/gateway: "true"` — musi działać na węźle z WiFi AP

### Zasoby

| | CPU | Memory |
|--|-----|--------|
| Requests | 150m | 192Mi |
| Limits | 500m | 384Mi |

### Wolumeny

| Wolumen | Typ | Punkt montowania | Opis |
|---------|-----|------------------|------|
| `sqlite-data` | PVC (`iot-security-sqlite`) | `/data` | Wspólna baza SQLite |
| `gateway-state` | hostPath `/var/lib/gateway-agent` | `/gateway-state` | Plik leases dnsmasq |
| `tmp` | emptyDir (Memory, 50Mi) | `/tmp` | Pliki pcap (tymczasowe) |

### Probes

- **Liveness**: `ip link show` — sprawdza czy interfejs sieciowy istnieje. Interwał 30s, initial delay 10s.
- Brak readiness probe (collector nie serwuje HTTP).

### Metryki

Endpoint Prometheus (`ENABLE_METRICS=false`) jest **wyłączony** w MVP, żeby nie kolidować z exporterami
na poziomie hosta (np. `node_exporter`).

---

## Powiązane dokumenty

- [ML Pipeline](ML_PIPELINE.md) — jak collector-owe flow-y są przetwarzane na feature-y i anomalie
- [Data Flow](DATA_FLOW.md) — end-to-end lineage danych od pakietu do dashboardu
- [Gateway Agent](GATEWAY_AGENT.md) — skąd biorą się leases DHCP
- [Infrastructure](INFRASTRUCTURE.md) — konfiguracja K8s i wolumeny
