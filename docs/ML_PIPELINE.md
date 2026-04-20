# ML Pipeline — Pełna dokumentacja

> Ostatnia aktualizacja: Kwiecień 2026

**Powiązane dokumenty:**
[Collector](COLLECTOR.md) · [Gateway Agent](GATEWAY_AGENT.md) · [Gateway API](GATEWAY_API.md) · [Dashboard](DASHBOARD.md) · [Infrastructure](INFRASTRUCTURE.md) · [Data Flow](DATA_FLOW.md)

---

## Spis treści

1. [Co to w ogóle jest?](#co-to-w-ogóle-jest)
2. [Architektura — jak dane płyną](#architektura--jak-dane-płyną)
3. [Feature Extraction — zamiana ruchu na liczby](#feature-extraction--zamiana-ruchu-na-liczby)
4. [Modele ML — jak wykrywamy anomalie](#modele-ml--jak-wykrywamy-anomalie)
5. [Risk Score — skąd się bierze procent ryzyka](#risk-score--skąd-się-bierze-procent-ryzyka)
6. [Behavior Alerts — co oznacza każdy alert](#behavior-alerts--co-oznacza-każdy-alert)
7. [Trening modeli — kiedy i jak](#trening-modeli--kiedy-i-jak)
8. [Inference — scoring w czasie rzeczywistym](#inference--scoring-w-czasie-rzeczywistym)
9. [Parametry i jak je tuningować](#parametry-i-jak-je-tuningować)
10. [Train Now — trening na żądanie](#train-now--trening-na-żądanie)
11. [Widok danych treningowych](#widok-danych-treningowych)
12. [Konfiguracja per device](#konfiguracja-per-device)
13. [Adaptive Threshold i Score Normalization](#adaptive-threshold-i-score-normalization)
14. [Tabele w bazie danych](#tabele-w-bazie-danych)
15. [API Endpoints (ML-related)](#api-endpoints-ml-related)
16. [Troubleshooting](#troubleshooting)
17. [Słownik pojęć](#słownik-pojęć)

---

## Co to w ogóle jest?

System wykrywa **anomalie w ruchu sieciowym** urządzeń IoT podłączonych do Twojego WiFi.

Prosta analogia: wyobraź sobie, że Twój inteligentny termostat normalnie łączy się z jednym serwerem producenta 3 razy dziennie. Nagle zaczyna się łączyć z 50 nieznanymi serwerami co minutę — to anomalia. Może termostat został zhakowany, albo po prostu aktualizuje firmware. System to wykrywa i mówi Ci: "hej, coś się zmieniło".

**Co system NIE robi:**
- Nie blokuje ruchu automatycznie (możesz ręcznie zablokować urządzenie)
- Nie skanuje zawartości pakietów (zero DPI, zero prywatności)
- Nie decyduje za Ciebie czy to atak — daje Ci wynik ryzyka (0–100%) i alerty

---

## Architektura — jak dane płyną

> Pełny diagram end-to-end: [Data Flow](DATA_FLOW.md)

```
  collector (tcpdump+tshark) → INSERT traffic_flows (SQLite)
       │
       ▼
  ml-trainer (CronJob co 30 min) → FeatureExtractor → fit() → .joblib
       │
       ▼
  ml-inference (loop co 60s) → score() + behavior_alerts → risk_score
       │
       ▼
  gateway-api → dashboard
```

**Kluczowe:** Trener i inference to **osobne procesy**. Nie komunikują się bezpośrednio. Dzielą:
- Bazę SQLite (`/data/iot-security.db`) — WAL mode pozwala czytać i pisać równocześnie
- Pliki modeli (`/data/models/`) — inference sprawdza mtime pliku co cykl

Szczegóły poszczególnych komponentów: [Collector](COLLECTOR.md), [Gateway Agent](GATEWAY_AGENT.md), [Gateway API](GATEWAY_API.md), [Dashboard](DASHBOARD.md).

---

## Feature Extraction — zamiana ruchu na liczby

> Jak surowe pola tshark mapują się na features: [Data Flow — Mapowanie](DATA_FLOW.md#mapowanie-pola-tshark--ml-features)

Surowe pakiety sieciowe (IP, port, rozmiar, DNS query) są **bezużyteczne** dla modelu ML. Trzeba je zamienić na **feature buckets** — podsumowania ruchu per urządzenie w oknach czasowych.

### Jak to działa krok po kroku

1. **Collector** zapisuje każdy pakiet jako wiersz w `traffic_flows`:
   ```
   device_id=3, dst_ip=8.8.8.8, dst_port=443, protocol=TCP,
   bytes_sent=1500, dns_query=google.com, timestamp=2026-04-20 14:03:22
   ```

2. **FeatureExtractor** grupuje flows w **5-minutowe okna** (buckety):
   ```
   device_id=3, bucket_start=2026-04-20 14:00:00
   ```

3. Dla każdego okna liczy **8 cech** (features):

| Feature | Co liczy | Przykład normalny | Przykład anomalii |
|---------|----------|-------------------|-------------------|
| `total_bytes` | Suma wszystkich bajtów | 15,000 | 5,000,000 |
| `packets` | Liczba pakietów | 50 | 10,000 |
| `unique_destinations` | Ile różnych IP docelowych | 3 | 150 |
| `unique_ports` | Ile różnych portów | 2 (80, 443) | 50 |
| `dns_queries` | Ile zapytań DNS | 5 | 500 |
| `avg_bytes_per_packet` | Średni rozmiar pakietu | 300 | 50 |
| `packet_rate` | Pakiety na sekundę | 0.17 | 33.3 |
| `connection_duration_avg` | Średni czas połączenia (ms) | 5000 | 50 |

### Co to jest bucket?

Bucket = jedno 5-minutowe okno czasowe. Jeśli urządzenie jest aktywne 24h, to masz **288 bucketów na dobę** (24 × 60 / 5 = 288).

Trening wymaga minimum **20 bucketów** per device (domyślnie). To oznacza ~100 minut aktywności urządzenia.

---

## Modele ML — jak wykrywamy anomalie

System trenuje **4 różne modele** per urządzenie. Każdy patrzy na dane z innej perspektywy.

### Isolation Forest (domyślny)

**Jak działa (prosta wersja):**
Wyobraź sobie grę w "20 pytań". Model losowo zadaje pytania typu "czy bytes > 1000?", "czy unique_ports < 5?". **Normalny** ruch potrzebuje wielu pytań żeby się odróżnić od reszty (bo jest "w tłumie"). **Anomalny** ruch wyróżnia się szybko — potrzeba mało pytań żeby go "wyizolować".

**Parametry:**
- `n_estimators` (domyślnie 200) — ile drzew pytań. Więcej = dokładniej ale wolniej.
- `contamination` (domyślnie 0.05) — jaki % danych uznajemy za anomalie podczas treningu.

**Kiedy jest dobry:** Ogólny detektor, dobrze radzi sobie z wieloma typami anomalii.
**Kiedy jest słaby:** Może nie wyłapać subtelnych zmian w zachowaniu.

### LOF (Local Outlier Factor)

**Jak działa (prosta wersja):**
Każdy punkt danych ma "sąsiadów" (inne buckety o podobnych wartościach). LOF sprawdza **gęstość** wokół punktu vs gęstość wokół sąsiadów. Jeśli Twój punkt jest w "pustym" miejscu (daleko od sąsiadów) — to anomalia.

**Parametry:**
- `n_neighbors` (auto, 5–20) — ile sąsiadów brać pod uwagę. Skaluje się z rozmiarem danych.
- `contamination` (domyślnie 0.05)

**Kiedy jest dobry:** Wykrywa anomalie lokalne (punkty, które są normalne globalnie ale dziwne w swojej okolicy).
**Kiedy jest słaby:** Wolniejszy na dużych zbiorach, wrażliwy na wymiarowość.

### OCSVM (One-Class SVM)

**Jak działa (prosta wersja):**
Rysuje "granicę" (hiperpowierzchnię) wokół normalnych danych w przestrzeni wielowymiarowej. Wszystko poza granicą = anomalia.

**Parametry:**
- `nu` (domyślnie 0.05) — odpowiednik contamination. Górna granica frakcji outlierów.

**Kiedy jest dobry:** Bardzo precyzyjna granica dla dobrze zdefiniowanych normalnych wzorców.
**Kiedy jest słaby:** Wolny na dużych zbiorach (O(n²)), wrażliwy na skalę danych.

### Autoencoder (sieć neuronowa)

**Jak działa (prosta wersja):**
Sieć neuronowa próbuje **skompresować** 8 features do mniejszej reprezentacji, a potem **odtworzyć** oryginał. Normalne dane — odtworzenie jest dobre (mały błąd). Anomalne dane — sieć ich nigdy nie widziała, więc odtworzenie jest kiepskie (duży błąd).

**Parametry:**
- `max_iter` (domyślnie 500) — ile kroków treningu.
- `contamination` (domyślnie 0.05) — do ustawienia threshold.

**Kiedy jest dobry:** Wyłapuje złożone, nieliniowe wzorce anomalii.
**Kiedy jest słaby:** Potrzebuje więcej danych, dłuższy trening, "czarna skrzynka" (trudno wyjaśnić dlaczego).

### Który model wybrać?

| Sytuacja | Rekomendacja |
|----------|-------------|
| Masz mało danych (< 50 bucketów) | Isolation Forest |
| Chcesz szybki trening na RPi | Isolation Forest |
| Urządzenie ma bardzo regularny wzorzec | OCSVM |
| Urządzenie ma zmienne ale przewidywalne zachowanie | LOF |
| Masz dużo danych (> 500 bucketów) i chcesz najlepszy wynik | Autoencoder |
| Nie wiesz / first time | Isolation Forest (domyślny) |

Możesz zmienić aktywny model per urządzenie w dashboardzie (dropdown na stronie device).

---

## Risk Score — skąd się bierze procent ryzyka

Risk score (0–100%) to **kompozytowy** wynik z 4 komponentów:

```
ml_risk          (0–35)   ← wynik modelu ML
+ behavior_risk  (0–35)   ← alerty heurystyczne (9 typów)
+ protocol_risk  (0–20)   ← alerty protokołowe (DNS/ICMP)
+ correlation    (0–15)   ← bonus gdy ML + heurystyki razem flagują
= final_risk     (0–100)
```

### ml_risk (0–35) — wynik modelu

- Score modelu (anomaly_score) jest porównywany z **adaptive threshold** (obliczanym per model z danych treningowych).
- Score **powyżej** threshold = normalny ruch → ml_risk 2–15% (baseline, żeby nie było 0.0)
- Score **na** threshold = granica → ml_risk = 35%
- Score **poniżej** threshold = anomalia → ml_risk 35–100% (skalowane)

### behavior_risk (0–35) — alerty heurystyczne

Suma kar z behavior alerts (patrz sekcja niżej). Przykład:
- `destination_novelty` (warning) → +10
- `dns_burst` (critical) → +15
- Suma ograniczona do 35.

### protocol_risk (0–20) — alerty protokołowe

Alerty związane z DNS/ICMP (patrz sekcja niżej). Oddzielone od behavior bo dotyczą warstwy protokołu:
- `dns_failure_spike` → +10
- `icmp_sweep_suspected` → +10
- Suma ograniczona do 20.

### correlation_bonus (0–15) — bonus korelacji

Kiedy **zarówno** model ML **i** heurystyki flagują to samo urządzenie, to prawdopodobieństwo prawdziwego problemu rośnie. Bonus = max(ml_risk, behavior_risk) × 0.3, cap 15.

---

## Behavior Alerts — co oznacza każdy alert

System ma **9 heurystycznych detektorów** które działają niezależnie od modeli ML. Nie wymagają treningu — porównują aktualne wartości z historią.

### Alerty Behavior (wpływają na behavior_risk, cap 35)

| Alert | Co wykrywa | Severity | Przykład |
|-------|-----------|----------|---------|
| `destination_novelty` | Nowe docelowe IP, których nie było w historii | warning/critical | Kamera zaczęła łączyć się z nowym serwerem w Chinach |
| `dns_burst` | Nagły skok zapytań DNS | warning/critical | Z 5 DNS/5min na 500 DNS/5min |
| `port_churn` | Nowe porty, których urządzenie wcześniej nie używało | warning | Urządzenie zaczęło używać portu 8443 |
| `traffic_pattern_drift` | Zmiana w profilu ruchu (bytes, packets) | warning | Z 10KB/5min na 5MB/5min |
| `beaconing_suspected` | Regularny, periodyczny ruch (C2-like) | warning/critical | Co 60 sekund dokładnie 200B do tego samego IP |

### Alerty Protocol (wpływają na protocol_risk, cap 20)

| Alert | Co wykrywa | Severity | Przykład |
|-------|-----------|----------|---------|
| `dns_failure_spike` | Wzrost nieudanych DNS (NXDOMAIN, SERVFAIL) | warning | 90% zapytań DNS zwraca błąd |
| `dns_nxdomain_burst` | Masowe NXDOMAIN (DGA-like) | warning/critical | 100 zapytań do losowych domen (qwxyz123.com) |
| `icmp_sweep_suspected` | Skanowanie sieci via ICMP echo | warning/critical | Ping do 50 adresów IP w 5 minut |
| `icmp_echo_fanout` | ICMP echo do wielu celów | warning | Ping do 20+ unikalnych IP |

---

## Trening modeli — kiedy i jak

### Automatyczny (CronJob)

- **Kiedy:** Co 30 minut (konfiguracja w `k8s/gateway/ml-trainer-cronjob.yaml`)
- **Co robi:**
  1. Pobiera traffic_flows z ostatnich `TRAINING_HOURS` (domyślnie 24h)
  2. Tworzy feature buckets (5-min okna)
  3. Dla każdego urządzenia z >= `MIN_TRAINING_SAMPLES` (domyślnie 20) bucketów:
     - Trenuje **wszystkie 4 modele** (IF, LOF, OCSVM, Autoencoder)
     - Zapisuje model + threshold + score_stats do `.joblib`
     - Zapisuje metryki do `model_metadata`
  4. Urządzenia z < 20 bucketów są pomijane (za mało danych)

### Ręczny (Train Now)

- **Jak:** Przycisk "Train Now" na stronie device w dashboardzie
- **Co robi:** Tworzy K8s Job z tymi samymi parametrami co CronJob, ale dla **jednego** urządzenia i **jednego** typu modelu
- **Kiedy używać:**
  - Po zmianie parametrów treningu
  - Gdy chcesz natychmiastowy wynik bez czekania 30 min
  - Po podłączeniu nowego urządzenia i zebraniu wystarczającej ilości danych

### Adaptive contamination

W trybie per-device, `contamination` nie jest stałe — jest obliczane adaptacyjnie:

```
adaptive_contamination = max(0.03, min(0.1, 5.0 / samples))
```

- Mało danych (20 bucketów) → contamination = 0.1 (10%) — bardziej agresywne wykrywanie
- Dużo danych (500 bucketów) → contamination = 0.03 (3%) — konserwatywne

Możesz nadpisać tę wartość per device (patrz [Konfiguracja per device](#konfiguracja-per-device)).

---

## Inference — scoring w czasie rzeczywistym

### Co robi inference loop

Inference działa jako ciągła pętla (Deployment, nie CronJob):

1. **Co 60 sekund** (konfigurowalny `INFERENCE_INTERVAL`):
   - Pobiera traffic_flows z ostatnich 24h
   - Tworzy feature buckets
   - Bierze **ostatni bucket** per device
   - Ładuje modele z dysku (cache na mtime)
   - Scoruje **wszystkie 4 modele** per device → zapisuje do `device_model_scores`
   - Dla **aktywnego modelu**: liczy risk_score, generuje behavior_alerts
   - Zapisuje wyniki: `devices.risk_score`, `device_inference_history`, `anomalies`, `device_behavior_alerts`
   - Czyści stare dane (retention: flows 7 dni, history 7 dni, alerts 14 dni)

2. **Heartbeat:** Pisze timestamp do `/tmp/inference-heartbeat` po każdym cyklu. K8s liveness probe sprawdza czy plik ma < 15 min.

### Jak inference reaguje na nowy model

Kiedy trainer zapisze nowy `.joblib`:
1. Inference w następnym cyklu zauważa zmianę mtime
2. Cache miss → ładuje nowy model
3. Od tego momentu scoruje nowym modelem

Nie wymaga restartu poda. Opóźnienie = max 60 sekund (jeden cykl inference).

---

## Parametry i jak je tuningować

### Globalna konfiguracja

Te wartości są domyślnymi dla wszystkich urządzeń. Możesz je zmienić na stronie Settings lub via API.

| Parametr | Domyślnie | Zakres | Co robi | Kiedy zmienić |
|----------|-----------|--------|---------|---------------|
| `contamination` | 0.05 (5%) | 0.01–0.20 | Jaki % danych treningowych uznajemy za anomalie. Wyższe = więcej anomalii wykrywanych, ale więcej false positives. | Za dużo false positives? Obniż do 0.02–0.03. Za mało wykryć? Podnieś do 0.08–0.10. |
| `n_estimators` | 200 | 50–500 | Ile "drzew pytań" w Isolation Forest. Więcej = dokładniej ale wolniejszy trening. | Na RPi z mało CPU zostaw 100–200. Na mocniejszej maszynie 300–500. |
| `training_hours` | 24 | 6–168 | Ile godzin wstecz patrzeć po dane do treningu. Dłuższe = więcej danych, stabilniejsze modele. | Nowe urządzenie? 6–12h. Stabilne środowisko? 48–168h. |
| `min_training_samples` | 20 | 5–100 | Ile bucketów minimum żeby trenować model. | Chcesz szybki start? 5–10 (ale model będzie słabszy). Chcesz dokładność? 50+. |
| `feature_bucket_minutes` | 2 | 1–10 | Szerokość okna czasowego bucketa w minutach. Krótszy = drobniejsza granulacja. | 1–2 min = drobne anomalie, więcej szumu. 5–10 min = stabilniejsze, mniej false positives. |

### Per-device overrides

Każde urządzenie może mieć **własne** wartości parametrów. Jeśli nie ustawisz override, używa globalnych defaults.

Kiedy warto nadpisać per device:
- **Kamera IP** — generuje stały, ciężki ruch → wyższy `contamination` (0.08) bo normalne spajki są częstsze
- **Czujnik temperatury** — bardzo regularny ruch → niższy `contamination` (0.02), każde odchylenie jest istotne
- **Telefon** — bardzo zmienny ruch → dłuższy `training_hours` (48–168h) żeby złapać pełny pattern

---

## Train Now — trening na żądanie

### Jak używać

1. Otwórz stronę device w dashboardzie
2. Wybierz model type z dropdown (domyślnie: aktywny model)
3. Kliknij "Train Now"
4. System tworzy K8s Job który:
   - Trenuje wybrany model dla tego urządzenia
   - Używa parametrów z per-device config (lub global defaults)
   - Status widoczny na stronie: "Training...", "Completed", "Failed"
5. Po zakończeniu (~10–60 sekund) inference automatycznie podchwyci nowy model

### Co się dzieje pod spodem

```
Dashboard → POST /api/devices/{id}/train?model_type=isolation_forest
         → gateway-api tworzy K8s Job:
           Image: ghcr.io/kacperjarocki/ml-pipeline
           Env: DEVICE_ID=3, MODEL_TYPE=isolation_forest, ...
         → Job startuje, trenuje, kończy się
         → TTL: 5 min po zakończeniu Job jest usuwany
```

### Kiedy NIE trenować

- Masz < 20 bucketów danych (sprawdź w "Training Data" view)
- Właśnie trenowałeś (poczekaj aż inference podchwyci nowy model, ~60s)
- Jest aktywna anomalia — nowe dane mogą "zatruć" model (model nauczy się że anomalia to norma)

---

## Widok danych treningowych

Na stronie device znajdziesz sekcję "Training Data" z:

### Summary
- **Total buckets:** ile 5-min bucketów jest dostępnych
- **Total flows:** ile surowych pakietów zebrano
- **Date range:** od kiedy do kiedy masz dane
- **Ready for training?** Tak/Nie (>= min_training_samples)

### Feature Statistics
Tabela z min/max/mean/p50/p95 per feature. Pozwala zobaczyć:
- Czy dane mają sensowny rozkład
- Czy nie ma outlierów w samych feature (np. bytes = 999999999)

### Latest Buckets
Ostatnie 20 bucketów z pełnymi wartościami features. Pozwala zobaczyć aktualne zachowanie urządzenia.

### Raw Flows (expandable)
Paginowana tabela surowych pakietów. 50 na stronę. Kolumny: timestamp, src_ip, dst_ip, protocol, bytes, dns_query.

---

## Konfiguracja per device

### Tabele konfiguracyjne

**`global_training_config`** — jeden wiersz, globalne defaults:
```
contamination=0.05, n_estimators=200, training_hours=24,
min_training_samples=20, feature_bucket_minutes=2
```

**`device_training_config`** — per device overrides (puste = użyj global):
```
device_id=3, contamination=0.08, training_hours=48
(reszta pól NULL = użyj global)
```

### Merge logic

```
effective_contamination = device_config.contamination ?? global_config.contamination
effective_n_estimators  = device_config.n_estimators  ?? global_config.n_estimators
...
```

### API

- `GET /api/v1/ml/config` — aktualne global defaults
- `PUT /api/v1/ml/config` — zmień global defaults
- `GET /api/v1/devices/{id}/training-config` — per-device (merged z global)
- `PUT /api/v1/devices/{id}/training-config` — ustaw per-device overrides

---

## Adaptive Threshold i Score Normalization

### Problem

Każdy model (IF, LOF, OCSVM, Autoencoder) produkuje scores na **zupełnie innej skali**:
- IsolationForest: typowo -0.5 do +0.5
- LOF: typowo -2.0 do +1.0
- OCSVM: typowo -1.0 do +1.0
- Autoencoder: z-scores, typowo -3.0 do +3.0

**Jeden globalny threshold (-0.5)** nie działa — OCSVM albo nigdy nie flaguje, albo flaguje wszystko.

### Rozwiązanie: Adaptive Threshold

Każdy model po treningu oblicza **swój threshold** z danych treningowych:

```
threshold = percentile(training_scores, contamination * 100)
```

Przykład: contamination=0.05, 200 training scores → threshold = 10ty najniższy score (5ty percentyl).

### Rozwiązanie: Score Normalization (z-score)

Żeby porównywać modele, surowe scores są mapowane na wspólną skalę (z-score):

```
z_score = (raw_score - mean) / std
```

Gdzie `mean` i `std` pochodzą z **rozkładu scores na danych treningowych**.

Z-score = 0 → score taki jak średnia treningowa (normalny).
Z-score = -2 → score 2 odchylenia standardowe poniżej średniej (prawdopodobnie anomalia).

Threshold też jest wyrażony jako z-score, więc `_risk_from_score(z_score, z_threshold)` działa identycznie niezależnie od modelu.

---

## Tabele w bazie danych

> Pełny schemat bazy danych: [Data Flow — Schemat bazy danych](DATA_FLOW.md#schemat-bazy-danych)

Tabele używane bezpośrednio przez ML pipeline:

| Tabela | Rola ML | Kto pisze | Kto czyta |
|--------|---------|-----------|-----------|
| `traffic_flows` | Surowe dane wejściowe | collector | ml-trainer, ml-inference |
| `model_metadata` | Metryki treningu | ml-trainer | gateway-api (ML Health) |
| `device_inference_history` | Historia wyników inference | ml-inference | gateway-api (wykresy) |
| `device_behavior_alerts` | Alerty heurystyczne | ml-inference | gateway-api (feed alertów) |
| `anomalies` | Anomalie ML | ml-inference | gateway-api (feed anomalii) |
| `devices` | Risk score | ml-inference (UPDATE) | gateway-api (dashboard) |
| `global_training_config` | Globalne parametry | gateway-api | ml-trainer, ml-inference |
| `device_training_config` | Per-device parametry | gateway-api | ml-trainer, ml-inference |

### `model_metadata` — metryki treningu

| Kolumna | Typ | Co przechowuje |
|---------|-----|----------------|
| device_id | INTEGER | Urządzenie (NULL = global model) |
| model_type | TEXT | isolation_forest / lof / ocsvm / autoencoder |
| trained_at | TEXT | Kiedy trenowano (ISO timestamp) |
| samples | INTEGER | Ile bucketów użyto |
| features | INTEGER | Ile features (8) |
| contamination | REAL | Użyta wartość contamination |
| threshold | REAL | Obliczony adaptive threshold |
| score_mean/std/p5/p50/p95 | REAL | Rozkład scores z treningu |
| estimated_anomaly_rate | REAL | Oczekiwana frakcja anomalii |
| training_hours | INTEGER | Lookback window (godziny) |
| version | TEXT NOT NULL | Wersja schematu (np. "1.0") — wymagana przez legacy constraint |

> **Uwaga schema:** Tabela `model_metadata` może zawierać kolumnę `version TEXT NOT NULL` jeśli została
> utworzona przez starszą wersję kodu. Kolumna ta nie jest zarządzana przez bieżące migracje i jest
> automatycznie wypełniana wartością `"1.0"` przy każdym zapisie metadanych treningu.

### `global_training_config` — globalne domyślne parametry

| Kolumna | Typ | Default |
|---------|-----|---------|
| contamination | REAL | 0.05 |
| n_estimators | INTEGER | 200 |
| training_hours | INTEGER | 24 |
| min_training_samples | INTEGER | 20 |
| feature_bucket_minutes | INTEGER | 2 |

### `device_training_config` — per-device override

| Kolumna | Typ | Notes |
|---------|-----|-------|
| device_id | INTEGER PK | FK → devices.id |
| contamination | REAL | NULL = użyj global |
| n_estimators | INTEGER | NULL = użyj global |
| training_hours | INTEGER | NULL = użyj global |
| min_training_samples | INTEGER | NULL = użyj global |
| feature_bucket_minutes | INTEGER | NULL = użyj global |

### Pozostałe tabele ML

Patrz `AGENTS.md` dla pełnych schematów: `devices`, `traffic_flows`, `anomalies`, `device_inference_history`, `device_behavior_alerts`, `device_model_scores`.

---

## API Endpoints (ML-related)

> Pełna lista endpointów API: [Gateway API](GATEWAY_API.md#endpointy)

### Training

| Method | Endpoint | Co robi |
|--------|----------|---------|
| POST | `/api/v1/devices/{id}/train` | Train Now — tworzy K8s Job |
| GET | `/api/v1/devices/{id}/train/status` | Status ostatniego Job |

### Configuration

| Method | Endpoint | Co robi |
|--------|----------|---------|
| GET | `/api/v1/ml/config` | Global training defaults |
| PUT | `/api/v1/ml/config` | Zmień global defaults |
| GET | `/api/v1/devices/{id}/training-config` | Per-device config (merged) |
| PUT | `/api/v1/devices/{id}/training-config` | Set per-device overrides |

### Training Data

| Method | Endpoint | Co robi |
|--------|----------|---------|
| GET | `/api/v1/devices/{id}/training-data` | Summary: buckets, flows, features stats |
| GET | `/api/v1/devices/{id}/raw-flows` | Paginowane raw flows |

### Observability

| Method | Endpoint | Co robi |
|--------|----------|---------|
| GET | `/api/v1/metrics/ml-status` | All devices ML status + training_metrics |
| GET | `/api/v1/devices/{id}/model-config` | Active model + model scores |
| GET | `/api/v1/devices/{id}/model-scores` | Per-model score history |
| GET | `/api/v1/devices/{id}/inference-history` | Inference history (7 days) |

---

## Troubleshooting

### "Model nie trenuje dla mojego urządzenia"

1. **Sprawdź ilość danych:** Otwórz device → Training Data. Potrzebujesz minimum 20 bucketów.
2. **Urządzenie jest podłączone?** Sprawdź connection badge. Jeśli "Not connected", collector nie zbiera danych.
3. **Za krótki training_hours:** Jeśli urządzenie podłączyłeś niedawno, ustaw `training_hours` na wartość mniejszą niż czas od podłączenia.
4. **Logi:** `kubectl logs -l app=ml-trainer -n iot-security --tail=50` — szukaj `training_not_enough_samples_for_device`.

### "Za dużo false positives (ciągle alerty na normalnych urządzeniach)"

1. **Obniż contamination:** Z 0.05 na 0.02–0.03. Mniej anomalii = mniej false positives.
2. **Wydłuż training_hours:** Z 24h na 48–168h. Model zobaczy więcej wariantów normalnego zachowania.
3. **Zmień model:** Isolation Forest jest bardziej konserwatywny niż LOF. Spróbuj IF zamiast LOF.
4. **Sprawdź behavior alerts:** Może alert `traffic_pattern_drift` jest zbyt agresywny — to heurystyka, nie ML.

### "Risk score nie rośnie mimo anomalii"

1. **Sprawdź aktywny model:** Dropdown na device page. Może aktywny model nie wykrywa anomalii, ale inny tak (patrz Model Comparison table).
2. **Sprawdź threshold:** ML Model Health → kolumna "Threshold". Jeśli threshold jest bardzo niski, model jest zbyt liberalny.
3. **Correlation bonus:** Risk rośnie mocniej gdy ML + heurystyki razem flagują. Tylko ML = max 35%.

### "Inference nie działa / pod restartuje się"

1. **Liveness probe:** `kubectl describe pod -l app=ml-inference -n iot-security` — sprawdź Events. Heartbeat file musi być < 15 min.
2. **Brak modeli:** Inference loguje `inference_model_missing_for_device` gdy brak modelu. Uruchom trening.
3. **DB locked:** SQLite busy_timeout=5000ms. Jeśli trainer i inference startują dokładnie w tym samym momencie, mogą się deadlockować (rzadkie).

### "Dashboard nie pokazuje ML Model Health"

1. **Proxy route:** Dashboard musi mieć route `/api/metrics/ml-status` w `main.py`.
2. **model_metadata pusty:** Tabela istnieje ale nie ma danych — trzeba poczekać na pierwszy trening (CronJob co 30 min) lub użyć Train Now.
3. **Legacy schema:** Jeśli tabela `model_metadata` była utworzona przez starszy kod, może mieć kolumnę `version TEXT NOT NULL`. Trening wtedy cicho failuje z `NOT NULL constraint failed: model_metadata.version`. Od wersji `4e8b24d` INSERT zawsze podaje `version = "1.0"` — wystarczy uruchomić trening ponownie.
4. **Network:** Dashboard → gateway-api connection. Sprawdź `GATEWAY_API_URL` env var.

---

## Słownik pojęć

| Pojęcie | Wyjaśnienie |
|---------|-------------|
| **Bucket** | Okno czasowe (domyślnie 5 min) w którym agregujemy ruch sieciowy jednego urządzenia. Jeden bucket = jeden wiersz danych dla ML. |
| **Feature** | Jedna liczba opisująca aspekt ruchu (np. total_bytes, packets). Model ML operuje na wektorach features. |
| **Contamination** | Jaki procent danych treningowych uznajemy za anomalie (domyślnie 5%). Wpływa na threshold — im wyższe, tym więcej flaguje. |
| **Threshold** | Granica: score poniżej threshold = anomalia. Obliczany automatycznie z danych treningowych (adaptive). |
| **Anomaly Score** | Wynik modelu ML dla jednego bucketa. Niższy = bardziej anomalny. Skala zależy od modelu (dlatego normalizujemy do z-score). |
| **Z-score** | Znormalizowany score: ile odchyleń standardowych od średniej treningowej. 0 = normalny, -2 = podejrzany, -3+ = prawie na pewno anomalia. |
| **Risk Score** | Kompozytowy wynik 0–100% łączący ML score, behavior alerts i protocol alerts. To jest główna liczba którą widzisz na dashboardzie. |
| **Behavior Alert** | Heurystyczny detektor (nie ML) — porównuje aktualne wartości z historią. Np. "nowa destynacja", "DNS burst". |
| **Inference** | Proces scorowania — ładuje wytrenowany model i ocenia aktualne dane. Loop co 60s. |
| **Training** | Proces uczenia modelu na historycznych danych. CronJob co 30 min lub ręcznie (Train Now). |
| **CronJob** | K8s zasób — uruchamia pod wg harmonogramu (np. co 30 min), pod kończy się po wykonaniu zadania. |
| **Deployment** | K8s zasób — utrzymuje N replik poda ciągle działających (np. inference loop, API). |
| **WAL mode** | SQLite Write-Ahead Logging — pozwala czytać z bazy podczas gdy ktoś inny pisze. Kluczowe bo trainer, inference i API współdzielą plik DB. |
| **Joblib** | Format pliku do serializacji modeli scikit-learn. Plik `.joblib` zawiera wytrenowany model + threshold + score stats. |
| **Adaptive threshold** | Threshold obliczany automatycznie z danych treningowych (percentyl na poziomie contamination). Każdy model ma swój. |
| **Per-device model** | Osobny model ML dla każdego urządzenia (zamiast jednego globalnego). Pozwala wykrywać anomalie specyficzne dla urządzenia. |

---

## Testowanie end-to-end (E2E)

### Przegląd

System posiada skrypt `scripts/verify-e2e.sh` który weryfikuje cały pipeline od collectora po alerty na dashboardzie.

**Problem sieciowy**: Będąc podłączonym do IoT WiFi AP, nie masz dostępu do API (za Traefik ingress). Dlatego skrypt działa w trzech fazach z pauzami na przełączenie sieci:

```
Faza 1 (off AP)  → Sprawdzenie stanu via API
     ↓  pauza: "Podłącz się do IoT WiFi AP, naciśnij Enter"
Faza 2 (on AP)   → Generowanie anomalnego ruchu
     ↓  pauza: "Odłącz się od AP, wróć do normalnej sieci, naciśnij Enter"
Faza 3 (off AP)  → Czekanie na inference + weryfikacja wyników
```

Skrypt automatycznie:

1. **Faza 1** — Sprawdza collector, trainer, inference, robi snapshot alertów
2. **Faza 2** — Generuje anomalny ruch (uruchamia `generate-anomaly-traffic.sh`)
3. **Faza 3** — Czeka na cykl inference, porównuje stan PRZED vs PO:
   - Risk score wzrósł?
   - Nowe anomalie utworzone?
   - Nowe behavior alerts?
4. Weryfikuje WebSocket endpoint dashboardu
5. Pokazuje health summary podów K8s

### Wymagania

- `curl` i `python3` zainstalowane
- API dostępne (Faza 1 i 3 — NIE na AP)
- Dostęp do IoT WiFi AP (Faza 2 — generowanie ruchu)
- `kubectl` skonfigurowane (opcjonalnie, dla pod health checks)
- Przynajmniej jeden wytrenowany model dla testowanego urządzenia

### Użycie

```bash
# Pełny test E2E — z pauzami na przełączenie sieci
./scripts/verify-e2e.sh

# Tylko weryfikacja stanu (bez generowania ruchu, bez pauz)
./scripts/verify-e2e.sh --skip-traffic

# Bez pauz — uruchamiasz z klastra lub masz dostęp do obu sieci
./scripts/verify-e2e.sh --no-pause

# Konkretne urządzenie + konkretny tryb ruchu
./scripts/verify-e2e.sh --device-id 2 --traffic-mode portscan

# Dłuższe czekanie na inference
./scripts/verify-e2e.sh --wait-minutes 10

# Verbose — pokaż surowe odpowiedzi API
./scripts/verify-e2e.sh --verbose
```

### Tryby generowania ruchu

Skrypt `scripts/generate-anomaly-traffic.sh` obsługuje 9 trybów, każdy celuje w inne heurystyki:

| Tryb | Alert types | Opis |
|------|-------------|------|
| `burst` | traffic_pattern_drift, dns_burst | Masowy HTTP + DNS |
| `spike` | traffic_pattern_drift | Max parallel downloads |
| `mix` | traffic_pattern_drift, dns_burst | HTTP + DNS przeplatane |
| `portscan` | port_churn | TCP probe na 16 portów |
| `dnsfail` | dns_failure_spike, dns_nxdomain_burst | NXDOMAIN queries |
| `icmpsweep` | icmp_sweep_suspected, icmp_echo_fanout | ICMP ping sweep |
| `beacon` | beaconing_suspected | Regularne małe requesty (10 min) |
| `novelty` | destination_novelty | Nowe IP/domeny |
| `full` | Wszystkie powyższe | Sekwencyjnie wszystkie tryby (~8 min) |

### Interpretacja wyników

- **PASS** — kontrolka przeszła pomyślnie
- **WARN** — kontrolka nie przeszła ale może to być oczekiwane (np. za mało danych baseline)
- **FAIL** — kontrolka nie przeszła — wymaga uwagi

### Typowe problemy

| Objaw | Przyczyna | Rozwiązanie |
|-------|-----------|-------------|
| "No trained models" | Za mało danych treningowych | Wygeneruj ruch (`burst` mode, 2h), poczekaj na CronJob |
| "Risk score still 0" | Inference nie scoruje | Sprawdź logi: `kubectl logs deploy/ml-inference -n iot-security` |
| "No new anomalies" | Threshold za wysoki / za mało baseline | Więcej normalnego ruchu → retraining → anomaly traffic |
| "No alerts in last hour" | Za mało historii (168h baseline) | System potrzebuje kilku dni normalnego ruchu |
| "Cannot reach API" | Podłączony do IoT AP / API down | Upewnij się, że NIE jesteś na IoT WiFi (API jest za Traefik ingress). Użyj `--no-pause` jeśli jesteś na klastrze |
| **Active model** | Który z 4 typów modelu jest używany do obliczania risk_score dla urządzenia. Domyślnie Isolation Forest. Można zmienić w UI. |
| **Score normalization** | Mapowanie surowych scores na z-score żeby porównywać modele na wspólnej skali. |
