# Testergebnisse: Azubi-Forecast (Prognose Zugaenge Seite)

- Ausgefuehrt am: 2026-03-10 15:37:21
- Ergebnis: 5/5 Checks bestanden

| Check | Thema | Status | Evidenz |
|---|---|---|---|
| C1 | August cycle causes post-August extension | PASS | Entry 2026-09-01 -> Graduation 2030-08-01 (~47.0 months total) |
| C2 | HC/MAK mismatch on Azubi_Hire is intentional | PASS | Azubi_Hire events=11, all count=+1 with mak=0.0 |
| C3 | Shared takeover debt influences baseline decisions | PASS | Observed outcomes=['exit', 'takeover']; sample=0:exit, 1:exit, 2:exit, 3:exit, 4:exit, 5:exit, 6:exit, 7:takeover |
| C4 | Jobfamily is overwritten to Sonstige in training phase | PASS | BAS1 Jobfamily: 'Azubi Spezial' -> 'Sonstige' |
| C5 | Conversion Out/In appears as two raw events but nets to zero HC | PASS | Out=-1, In=1, Net=0 |

## Kurzfazit

- Alle Deep-Dive-Checks konnten reproduzierbar bestaetigt werden.
