# gateway-api

`gateway-api` jest główną warstwą API systemu. Łączy dane z:

- SQLite (`devices`, `traffic_flows`, `anomalies`)
- `gateway-agent`
- modeli ML zapisanych na współdzielonym storage

## Główne odpowiedzialności

- urządzenia i ich status
- anomalie
- metryki dashboardu
- konfiguracja Wi-Fi gatewaya
- status modeli ML

## Główne grupy endpointów

- `/api/v1/devices`
- `/api/v1/anomalies`
- `/api/v1/metrics/*`
- `/api/v1/gateway/wifi/*`

## Device Explainability

Na device detail API są dostępne dodatkowe endpointy:

- `/api/v1/devices/{id}/behavior-alerts`
- `/api/v1/devices/{id}/risk-contributors`
- `/api/v1/devices/{id}/behavior-baseline`
- `/api/v1/devices/{id}/protocol-signals`

Te endpointy składają razem wynik ML, heurystyki behavior i protocol-level signals z collectora.

`/api/v1/devices/{id}/risk-contributors` zwraca teraz także:

- `ml_risk`
- `behavior_risk`
- `protocol_risk`
- `correlation_bonus`
- `previous_risk_score`
- `risk_delta`
- `status`
- `top_reason`

Contributors są deduplikowani po `alert_type` i dostają `effective_score` z decay czasowym, żeby API pokazywało aktywny wpływ na bieżący risk, a nie tylko historię alertów.

## Presence Model

Widoczność urządzeń opiera się na:

1. DHCP lease z `gateway-agent`
2. fallback do recent traffic

Urządzenia mogą być chwilowo syntetyczne, zanim collector zapisze trwały rekord w DB.

## ML Status

`/api/v1/metrics/ml-status` pokazuje:

- gdzie są modele
- ile modeli jest gotowych
- które urządzenia mają `model_status=ready`

## Konfiguracja Wi-Fi

Router `gateway/wifi` deleguje operacje do `gateway-agent`:

- `GET/PUT /config`
- `POST /validate`
- `POST /apply`
- `POST /rollback`
- `GET /status`

## Troubleshooting

Najważniejsze endpointy diagnostyczne:

- `/health`
- `/api/v1/devices`
- `/api/v1/metrics/summary`
- `/api/v1/metrics/timeline`
- `/api/v1/metrics/ml-status`
- `/api/v1/gateway/wifi/status`
