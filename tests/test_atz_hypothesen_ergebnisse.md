# Ergebnis: Hypothesentest ATZ (Prognose Abgaenge)

- Ausgefuehrt am: 2026-03-09 19:02:01
- Testfokus: Altersteilzeit-Mechanik (Eligibility, Matrix, Eventlogik, Interaktion Rente)
- Ergebnis: 7/7 Hypothesen bestaetigt

## Zusammenfassung

| Hypothese | Titel | Status | Kernergebnis |
|---|---|---|---|
| H1 | Altersband steuert Eligibility | PASS | ATZ-Pool korrekt auf Altersband begrenzt: ['001002', '001003'] |
| H2 | Sonder-OE-Codes werden ausgeschlossen | PASS | Sonder-OE gefiltert, gezogene IDs: ['001101'] |
| H3 | Ruhend und bestehende ATZ-Faelle sind von Neufaellen ausgeschlossen | PASS | Nur aktiver Nicht-ATZ-MA neu geplant; bestehender ATZ unveraendert. IDs: ['001201', '001203'] |
| H4 | ATZ-Matrix (JobFamily) uebersteuert Basisrate | PASS | JobFamily-Matrix greift korrekt: ['001301'] |
| H5 | ATZ-Matrix (OrgUnit) mit Default-Fallback funktioniert | PASS | OrgUnit-Matrix inkl. Default-Fallback korrekt: ['001401'] |
| H6 | ATZ-Events folgen AR->FR vor ATZ_END | PASS | Event-Reihenfolge ok: AR->FR am 2026-02-28, ATZ_END am 2026-04-30 |
| H7 | In-ATZ schliesst direkte Rentenlogik aus | PASS | Direkte Rente greift nur fuer Nicht-ATZ: ['001602'] |

## Kurzfazit

- Alle getesteten Soll-Hypothesen wurden durch das aktuelle Verhalten der Engine bestaetigt.

## Methodik-Hinweis

- Fuer stabile Reproduzierbarkeit wurden deterministische Seeds verwendet.
- In einzelnen Tests wurden hohe Raten genutzt, um stochastische Ziehungen robust sichtbar zu machen.
