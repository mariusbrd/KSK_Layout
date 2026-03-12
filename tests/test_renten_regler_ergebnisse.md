# Ergebnis: Renten-Regler Tests (Prognose Abgaenge)

- Ausgefuehrt am: 2026-03-09 18:32:26
- Engine: `abgaenge.forecast.run_forecast_abgaenge`
- Fokus: Regler `rent_rate_65` und `rent_rate_60_65`

## Szenario-Ergebnisse

| Szenario | rent_rate_65 | rent_rate_60_65 | Erwartet | Beobachtet | Status |
|---|---:|---:|---|---|---|
| S1_00_00 | 0.00 | 0.00 | 0 (-) | 0 (-) | PASS |
| S2_10_00 | 1.00 | 0.00 | 2 (001001, 001002) | 2 (001001, 001002) | PASS |
| S3_00_10 | 0.00 | 1.00 | 2 (002001, 002002) | 2 (002001, 002002) | PASS |
| S4_10_10 | 1.00 | 1.00 | 4 (001001, 001002, 002001, 002002) | 4 (001001, 001002, 002001, 002002) | PASS |

## Kurzfazit

- Die Rentenfunktion reagiert erwartungsgemaess auf beide Regler in den getesteten Grenzfaellen.
- Bei `rent_rate_65=1.0` werden ausschliesslich 65+ Faelle gezogen (wenn `rent_rate_60_65=0.0`).
- Bei `rent_rate_60_65=1.0` werden ausschliesslich 60-64 Faelle gezogen (wenn `rent_rate_65=0.0`).
- Mitarbeiter unter 60 wurden in keinem Szenario als Rentenabgang verarbeitet.
