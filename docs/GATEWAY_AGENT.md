# Gateway Agent — WiFi AP + DHCP + NAT

> Ostatnia aktualizacja: Kwiecień 2026

---

## Spis treści

1. [Co robi gateway-agent?](#co-robi-gateway-agent)
2. [Zarządzane procesy](#zarządzane-procesy)
3. [Konfiguracja WiFi AP (hostapd)](#konfiguracja-wifi-ap-hostapd)
4. [DHCP i DNS (dnsmasq)](#dhcp-i-dns-dnsmasq)
5. [NAT i iptables](#nat-i-iptables)
6. [Blokowanie urządzeń](#blokowanie-urządzeń)
7. [Apply i Rollback](#apply-i-rollback)
8. [API endpointy](#api-endpointy)
9. [Pliki stanu](#pliki-stanu)
10. [Konfiguracja](#konfiguracja)
11. [Wdrożenie K8s](#wdrożenie-k8s)

---

## Co robi gateway-agent?

Gateway-agent zarządza **infrastrukturą sieciową** na węźle gateway — uruchamia punkt dostępowy WiFi,
serwer DHCP/DNS, i konfiguruje NAT (maskaradę). Jest to jedyny komponent, który bezpośrednio
kontroluje procesy systemowe (hostapd, dnsmasq) i reguły iptables.

Oprócz zarządzania siecią, gateway-agent udostępnia REST API (port 7000) do:
- Konfiguracji WiFi (SSID, hasło, kanał)
- Blokowania/odblokowywania urządzeń po MAC
- Walidacji konfiguracji przed zastosowaniem
- Rollbacku do ostatniej działającej konfiguracji

---

## Zarządzane procesy

Gateway-agent uruchamia i nadzoruje **trzy** procesy/podsystemy:

| Proces | Narzędzie | Funkcja |
|--------|-----------|---------|
| WiFi AP | hostapd | Punkt dostępowy 802.11n (WPA2-PSK) |
| DHCP/DNS | dnsmasq | Przydzielanie adresów IP + cache DNS |
| NAT | iptables | Maskarada i forwarding pakietów |

Procesy są uruchamiane w kolejności: interfejs → iptables → hostapd → dnsmasq.
Przy zatrzymaniu kolejność jest odwrócona.

---

## Konfiguracja WiFi AP (hostapd)

### Pola konfiguracyjne (WifiConfig)

| Pole | Typ | Domyślna | Walidacja |
|------|-----|----------|-----------|
| `ssid` | str | *(wymagane)* | 1–32 znaki |
| `psk` | str | *(wymagane)* | 8–63 znaki |
| `country_code` | str | `PL` | Dokładnie 2 znaki |
| `channel` | int | `6` | 1–165 |
| `ap_interface` | str | `wlan0` | Min 1 znak, nie może być `eth0` |
| `upstream_interface` | str | `eth0` | Min 1 znak |
| `subnet_cidr` | str | `192.168.50.0/24` | Musi być poprawną siecią |
| `gateway_ip` | str | `192.168.50.1` | Musi być w podsieci, poza zakresem DHCP |
| `dhcp_range_start` | str | `192.168.50.100` | Musi być w podsieci |
| `dhcp_range_end` | str | `192.168.50.200` | Musi być >= start |
| `enabled` | bool | `True` | Jeśli false — zatrzymuje usługi i czyści reguły |

### Generowany hostapd.conf

```ini
interface=wlan0
driver=nl80211
ctrl_interface=/var/run/hostapd
ctrl_interface_group=0
ssid=<ssid>
country_code=<country_code>
channel=<channel>
hw_mode=g
ieee80211n=1
wmm_enabled=1
ignore_broadcast_ssid=0
auth_algs=1
wpa=2
wpa_passphrase=<psk>
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
```

Stałe: `hw_mode=g` (2.4 GHz), `ieee80211n=1` (802.11n), `wpa=2` (WPA2 only),
`rsn_pairwise=CCMP` (AES).

---

## DHCP i DNS (dnsmasq)

### Generowany dnsmasq.conf

```ini
domain-needed
bogus-priv
no-resolv
dhcp-authoritative
server=1.1.1.1
server=8.8.8.8
log-dhcp
log-queries
interface=<ap_interface>
bind-interfaces
except-interface=lo
dhcp-leasefile=/data/dnsmasq.leases
dhcp-range=<start>,<end>,<netmask>,12h
dhcp-option=option:router,<gateway_ip>
dhcp-option=option:dns-server,<gateway_ip>
```

### Format pliku leases

Plik `/data/dnsmasq.leases` (hostPath) jest odczytywany przez collector do identyfikacji urządzeń.

Format linii:
```
<timestamp> <mac_address> <ip_address> <hostname> <client_id>
```

Przykład:
```
1713600000 aa:bb:cc:dd:ee:ff 192.168.50.101 my-phone 01:aa:bb:cc:dd:ee:ff
```

### Jak leases trafiają do collectora?

```
gateway-agent                        collector
     │                                   │
     │  hostPath: /var/lib/gateway-agent  │
     │  mount:    /data                   │
     │  →  /data/dnsmasq.leases          │
     │                                   │
     │           hostPath mount           │
     │  /var/lib/gateway-agent            │
     │  →  /gateway-state                 │
     │                                   │
     └───────────────────────────────────►│
        /gateway-state/dnsmasq.leases     │
```

Oba pody montują ten sam katalog hosta (`/var/lib/gateway-agent`), ale pod różnymi ścieżkami.

---

## NAT i iptables

Gateway-agent tworzy **3 własne łańcuchy** iptables:

### Łańcuchy

| Łańcuch | Tabela | Funkcja |
|---------|--------|---------|
| `IOT_GATEWAY_NAT` | nat | Reguła MASQUERADE — ruch z podsieci IoT wychodzi na upstream z IP gatewaya |
| `IOT_GATEWAY_FWD` | filter | ACCEPT forwarding między interfejsem AP a upstream + conntrack RELATED,ESTABLISHED |
| `IOT_DEVICE_BLOCK` | filter | Per-MAC reguły DROP dla zablokowanych urządzeń |

### Kolejność ewaluacji w łańcuchu FORWARD

```
FORWARD
   ├── 1. IOT_DEVICE_BLOCK  ← sprawdza blokady (pozycja 1, najwyższy priorytet)
   ├── 2. IOT_GATEWAY_FWD   ← pozwala na normalny forwarding
   └── ... (domyślna polityka)

POSTROUTING (nat)
   └── IOT_GATEWAY_NAT      ← maskarada
```

`IOT_DEVICE_BLOCK` jest wstawiony na **pozycję 1** w łańcuchu FORWARD, więc jest sprawdzany
**przed** `IOT_GATEWAY_FWD`. Zablokowane urządzenie nigdy nie dotrze do reguły ACCEPT.

### Reguły w IOT_GATEWAY_FWD

1. `ACCEPT` — ruch z `ap_interface` do `upstream_interface` (wychodzący z IoT)
2. `ACCEPT` — conntrack `RELATED,ESTABLISHED` z `upstream_interface` do `ap_interface` (odpowiedzi)

---

## Blokowanie urządzeń

Blokowanie działa przez dodawanie reguły DROP w łańcuchu `IOT_DEVICE_BLOCK`:

```
# Blokowanie
iptables -A IOT_DEVICE_BLOCK -m mac --mac-source aa:bb:cc:dd:ee:ff -j DROP

# Odblokowanie
iptables -D IOT_DEVICE_BLOCK -m mac --mac-source aa:bb:cc:dd:ee:ff -j DROP
```

Blokada dotyczy **całego ruchu** urządzenia (nie per-port ani per-protokół). Urządzenie
pozostaje podłączone do WiFi, ale nie może komunikować się z internetem ani innymi urządzeniami.

---

## Apply i Rollback

### Apply — zastosowanie nowej konfiguracji

Proces `POST /apply`:

```
1. Walidacja konfiguracji (pola, zakresy IP, subnet)
2. Renderowanie hostapd.conf i dnsmasq.conf
3. Zatrzymanie istniejących procesów (jeśli działają)
4. Konfiguracja interfejsu AP (ip addr, ip link up)
5. Ustawienie reguł iptables (NAT + forwarding)
6. Uruchomienie hostapd
7. Uruchomienie dnsmasq
8. Health check — czy procesy żyją
9. Zapis do last_known_good/ (snapshot na wypadek rollbacku)
10. Zapis wifi_config.json
```

Jeśli krok 7 lub 8 się nie powiedzie, stan jest nieokreślony — dlatego istnieje rollback.

### Rollback — przywrócenie ostatniej działającej konfiguracji

Proces `POST /rollback`:

```
1. Odczytanie plików z last_known_good/
2. Powtórzenie kroków 3–8 z apply (ale z przywróconymi plikami)
```

### Auto-restore przy starcie

Jeśli `AUTO_RESTORE=true` i `ENABLE_APPLY=true`, gateway-agent przy starcie automatycznie
odczytuje `wifi_config.json` i wykonuje apply. To przywraca WiFi AP po restarcie poda.

---

## API endpointy

Wszystkie endpointy nasłuchują na **porcie 7000**.

| Metoda | Ścieżka | Opis | Zabezpieczenie |
|--------|---------|------|----------------|
| `GET` | `/health` | `{"status":"ok"}` | — |
| `GET` | `/status` | Pełny status: interfejsy, procesy, config, leases | — |
| `POST` | `/validate` | Walidacja konfiguracji WiFi (bez stosowania) | — |
| `POST` | `/apply` | Zastosowanie nowej konfiguracji WiFi | `ENABLE_APPLY=true` wymagane (403) |
| `POST` | `/rollback` | Przywrócenie last-known-good | `ENABLE_APPLY=true` wymagane (403) |
| `POST` | `/block` | Blokowanie urządzenia po MAC | — |
| `DELETE` | `/block/{mac}` | Odblokowanie urządzenia | — |
| `GET` | `/blocked` | Lista zablokowanych adresów MAC | — |

### Parametry `/status`

| Query param | Domyślna | Opis |
|-------------|----------|------|
| `ap_interface` | `wlan0` | Interfejs AP do sprawdzenia |
| `upstream_interface` | `eth0` | Interfejs upstream do sprawdzenia |

### Bezpieczeństwo

`ENABLE_APPLY` to **safety gate** — domyślnie `false`. W K8s jest ustawione na `true` tylko
w overlay `gateway-prod` (plik `gateway-agent-enable-apply.yaml`). Zapobiega przypadkowemu
zastosowaniu konfiguracji WiFi w środowisku deweloperskim.

---

## Pliki stanu

Wszystkie pliki stanu są przechowywane w `STATE_DIR` (domyślnie `/data`):

| Ścieżka | Opis |
|---------|------|
| `$STATE_DIR/lock` | Plik blokady |
| `$STATE_DIR/wifi_config.json` | Aktualna konfiguracja WiFi (JSON) |
| `$STATE_DIR/hostapd.conf` | Wyrenderowany config hostapd |
| `$STATE_DIR/dnsmasq.conf` | Wyrenderowany config dnsmasq |
| `$STATE_DIR/dnsmasq.leases` | Plik leases DHCP (zarządzany przez dnsmasq) |
| `$STATE_DIR/last_apply.json` | Status ostatniego apply: `{ok, message, ts}` |
| `$STATE_DIR/last_known_good/` | Katalog snapshot-u (kopia config + hostapd + dnsmasq) |

---

## Konfiguracja

### Zmienne środowiskowe

| Zmienna | Domyślna | Opis |
|---------|----------|------|
| `ENABLE_APPLY` | `false` | Safety gate — musi być `true` do apply/rollback |
| `AUTO_RESTORE` | `true` | Automatyczne przywracanie konfiguracji przy starcie |
| `STATE_DIR` | `/data` | Katalog na pliki stanu |

### Wartości produkcyjne (overlay gateway-prod)

| Zmienna | Wartość |
|---------|--------|
| `ENABLE_APPLY` | `true` |
| `AUTO_RESTORE` | `true` |

---

## Wdrożenie K8s

### Wymagania

Gateway-agent **musi** mieć:
- `hostNetwork: true` — zarządza interfejsami sieciowymi hosta
- `hostPID: true` — widoczność procesów hosta
- `privileged: true` — iptables, hostapd, dnsmasq wymagają uprawnień root
- `nodeSelector: node-role.kubernetes.io/gateway: "true"` — musi działać na węźle z kartą WiFi

### Zasoby

| | CPU | Memory |
|--|-----|--------|
| Requests | 20m | 64Mi |
| Limits | 200m | 256Mi |

### Wolumen

| Wolumen | Typ | Punkt montowania | Opis |
|---------|-----|------------------|------|
| hostPath | `/var/lib/gateway-agent` (DirectoryOrCreate) | `/data` | Pliki stanu + leases |

**Uwaga**: Gateway-agent **nie** używa PVC `iot-security-sqlite`. Używa `hostPath`, żeby pliki
przetrwały restart poda i były dostępne dla collectora (który też montuje ten katalog).

### Probes

- **Liveness**: `GET /health:7000`, interwał 30s, initial delay 10s
- **Readiness**: `GET /health:7000`, interwał 10s, initial delay 3s
- **preStop hook**: `sleep 5` — czas na dokończenie bieżących operacji

### Strategia aktualizacji

`RollingUpdate` z `maxUnavailable=1`, `maxSurge=0` — nigdy dwa pody jednocześnie
(bo oba miałyby hostNetwork i konflikty portów).

`terminationGracePeriodSeconds: 20` — czas na zamknięcie procesów hostapd/dnsmasq.

---

## Powiązane dokumenty

- [Collector](COLLECTOR.md) — jak collector czyta leases z gateway-agent
- [Gateway API](GATEWAY_API.md) — jak API proxy-uje żądania do gateway-agent
- [Infrastructure](INFRASTRUCTURE.md) — konfiguracja K8s
- [Data Flow](DATA_FLOW.md) — rola gateway-agent w przepływie danych
