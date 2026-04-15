# gateway-agent

`gateway-agent` zarządza warstwą Wi-Fi gatewaya na Raspberry Pi. Odpowiada za:

- konfigurację AP (`hostapd`)
- konfigurację DHCP (`dnsmasq`)
- ustawienie adresu IP na interfejsie AP
- włączenie NAT/forwardingu do interfejsu upstream
- status, validate, apply i rollback konfiguracji Wi-Fi

## Runtime

Kontener działa jako uprzywilejowany workload na gateway node:

- `hostNetwork: true`
- `hostPID: true`
- `privileged: true`

Stan jest przechowywany w katalogu `STATE_DIR` (domyślnie `/data`):

- `wifi_config.json`
- `hostapd.conf`
- `dnsmasq.conf`
- `dnsmasq.leases`
- `last_apply.json`
- `last_known_good/`

## API

Endpointy FastAPI:

- `GET /health`
- `GET /status`
- `POST /validate`
- `POST /apply`
- `POST /rollback`

`/status` zwraca między innymi:

- stan `hostapd`
- stan `dnsmasq`
- IP AP
- `connected_clients`
- `lease_count`
- `active_config`

## Apply Flow

`apply` wykonuje:

1. zapis configu i render plików `hostapd`/`dnsmasq`
2. konfigurację IP na interfejsie AP
3. włączenie `net.ipv4.ip_forward=1`
4. konfigurację dedykowanych chainów iptables
5. restart procesów `dnsmasq` i `hostapd`
6. zapis `last-known-good`

`rollback` przywraca ostatnią poprawną konfigurację.

## Startup / Shutdown

- przy `ENABLE_APPLY=true` i `AUTO_RESTORE=true` agent próbuje odtworzyć ostatnią konfigurację przy starcie
- przy shutdownie agent jawnie zatrzymuje `hostapd` i `dnsmasq`

## Kluczowe zmienne środowiskowe

- `ENABLE_APPLY`
- `AUTO_RESTORE`
- `STATE_DIR`

## Troubleshooting

Najważniejsze rzeczy do sprawdzenia:

1. `GET /status`
2. `hostapd.running`
3. `dnsmasq.running`
4. `last_apply_message`
5. `connected_clients`

Jeśli SSID nie jest widoczne:

- sprawdź, czy wdrożony jest overlay z `ENABLE_APPLY=true`
- sprawdź `hostapd.last_error`
- sprawdź, czy `wlan0` istnieje i wspiera AP mode
