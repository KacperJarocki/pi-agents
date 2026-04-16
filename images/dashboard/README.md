# dashboard

`dashboard` to web UI systemu. Renderuje dane z `gateway-api` i pokazuje:

- urządzenia
- timeline ruchu
- top talkers
- anomalie
- konfigurację Wi-Fi gatewaya

## Widoki

- `/` - główny dashboard
- `/devices/{id}` - SOC-like console dla pojedynczego urządzenia
- `/gateway` - konfiguracja Wi-Fi i status gatewaya

## Dane i badge

Dashboard pokazuje:

- `Connected via dhcp_lease`
- `Connected via recent_traffic`
- `Model ready`
- `Model missing`

Na stronie urządzenia dashboard pokazuje też:

- `Behavior Alerts`
- `Risk Contributors`
- `Behavior Baseline`
- `Protocol Signals`

## Mechanika

- FastAPI + Jinja2
- częściowe odświeżanie przez HTMX
- polling API dla danych runtime

## Główne partiale

- `/partial/devices`
- `/partial/anomalies`
- `/partial/timeline`
- `/partial/top-talkers`

## Troubleshooting

Jeśli UI nie pokazuje danych:

1. sprawdź `/health` dashboardu
2. sprawdź `/health` i `/api/v1/*` po stronie `gateway-api`
3. sprawdź `/api/v1/metrics/ml-status` dla stanu modeli
