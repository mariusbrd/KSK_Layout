"""
Smoke-Tests fuer die Zahlenparser-Logik aus _numeric_compensation_series()
in pages/1_Kompakt.py.

Die Funktion wird hier standalone repliziert (kein Streamlit-Import noetig),
damit dieser Test ohne laufende App ausfuehrbar ist.

Run:
  py KSK_Layout/tests/test_numeric_compensation_series.py
"""

import sys
import math
import pandas as pd


# ---------------------------------------------------------------------------
# Standalone-Replik der Funktion (spiegelt exakt pages/1_Kompakt.py)
# Aenderungen hier muessen auch dort nachgezogen werden (und umgekehrt).
# ---------------------------------------------------------------------------

def _numeric_compensation_series(
    series: pd.Series, default: float = 0.0, kind: str = "fte"
) -> pd.Series:
    text = series.astype("string").str.strip()
    text = text.str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)

    has_comma = text.str.contains(",", regex=False).fillna(False)
    has_dot   = text.str.contains(".", regex=False).fillna(False)
    both      = has_comma & has_dot

    normalized = text.copy()

    last_is_comma = both & text.str.match(r"^-?[\d.]+,\d+$").fillna(False)
    last_is_dot   = both & text.str.match(r"^-?[\d,]+\.\d+$").fillna(False)

    normalized = normalized.where(
        ~last_is_comma,
        text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
    )
    normalized = normalized.where(
        ~last_is_dot,
        text.str.replace(",", "", regex=False),
    )

    only_comma = has_comma & ~has_dot
    normalized = normalized.where(
        ~only_comma,
        text.str.replace(",", ".", regex=False),
    )

    only_dot  = ~has_comma & has_dot
    multi_dot = only_dot & normalized.str.count(r"\.").gt(1).fillna(False)
    if kind == "currency":
        single_dot_thousands = (
            only_dot
            & normalized.str.match(r"^-?[1-9]\d{0,2}\.\d{3}$").fillna(False)
        )
    else:
        single_dot_thousands = pd.Series(False, index=normalized.index)

    dot_thousands = multi_dot | single_dot_thousands
    normalized = normalized.where(~dot_thousands, normalized.str.replace(".", "", regex=False))

    return pd.to_numeric(normalized, errors="coerce").fillna(default)


def _parse(value: str, kind: str = "fte") -> float:
    return _numeric_compensation_series(pd.Series([value]), kind=kind).iloc[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

PASS = 0
FAIL = 0


def check(label: str, got: float, expected: float, tol: float = 1e-9) -> None:
    global PASS, FAIL
    is_nan_got = isinstance(got, float) and math.isnan(got)
    is_nan_exp = isinstance(expected, float) and math.isnan(expected)
    if is_nan_got and is_nan_exp:
        ok = True
    elif is_nan_got or is_nan_exp:
        ok = False
    else:
        ok = math.isclose(float(got), float(expected), rel_tol=tol)
    status = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{status}] {label}: got={float(got)!r}  expected={float(expected)!r}")


print("=== kind='fte' (MAK/FTE/Sollarbeitszeit) ===")

# Kritischer Fix: 0.141 darf NICHT zu 141.0 werden
check("0.141 -> 0.141",        _parse("0.141",         "fte"), 0.141)
check("0,141 -> 0.141",        _parse("0,141",         "fte"), 0.141)
check("0.333 -> 0.333",        _parse("0.333",         "fte"), 0.333)
check("0.500 -> 0.5",          _parse("0.500",         "fte"), 0.5)
check("0.641 -> 0.641",        _parse("0.641",         "fte"), 0.641)

# 1.000 im FTE-Kontext = 1.0 (voller MAK), nicht 1000
check("1.000 -> 1.0 (fte)",    _parse("1.000",         "fte"), 1.0)
# 1,000 im deutschen Kontext: Komma = Dezimaltrennzeichen -> 1.0
check("1,000 -> 1.0 (de)",     _parse("1,000",         "fte"), 1.0)

check("1.234 -> 1.234 (fte)",  _parse("1.234",         "fte"), 1.234)
check("1,234 -> 1.234 (de)",   _parse("1,234",         "fte"), 1.234)
check("762.91 -> 762.91",      _parse("762.91",        "fte"), 762.91)
check("762,91 -> 762.91",      _parse("762,91",        "fte"), 762.91)
check("1234.56 -> 1234.56",    _parse("1234.56",       "fte"), 1234.56)
check("1234,56 -> 1234.56",    _parse("1234,56",       "fte"), 1234.56)

# Gemischte Formate (letzter Trenner bestimmt)
check("1.234,56 -> 1234.56",   _parse("1.234,56",      "fte"), 1234.56)
check("1,234.56 -> 1234.56",   _parse("1,234.56",      "fte"), 1234.56)
check("59.674.856,62 -> ...",  _parse("59.674.856,62", "fte"), 59674856.62)
check("59,674,856.62 -> ...",  _parse("59,674,856.62", "fte"), 59674856.62)

print()
print("=== kind='currency' (EUR/Kosten) ===")

# 1.000 im Kosten-Kontext = 1000 EUR (Tausender)
check("1.000 -> 1000.0 (eur)", _parse("1.000",         "currency"), 1000.0)

# 1,000 mit nur Komma: im deutschen Kontext ist Komma = Dezimal -> 1.0
# Wuerde im englischen Kontext 1000 bedeuten, aber das ist in deutschen Daten selten.
# Dokumentierter Grenzfall: 1,000 -> 1.0 (Komma-Dezimal-Annahme, deutsches Locale).
check("1,000 -> 1.0 (de, ambig)", _parse("1,000",     "currency"), 1.0)

# Fuehrende Null: 0.141 bleibt Dezimal auch im EUR-Kontext
check("0.141 -> 0.141 (eur)",  _parse("0.141",         "currency"), 0.141)

check("762.91 -> 762.91",      _parse("762.91",        "currency"), 762.91)
check("762,91 -> 762.91",      _parse("762,91",        "currency"), 762.91)
check("1.234,56 -> 1234.56",   _parse("1.234,56",      "currency"), 1234.56)
check("1,234.56 -> 1234.56",   _parse("1,234.56",      "currency"), 1234.56)
check("1234.56 -> 1234.56",    _parse("1234.56",       "currency"), 1234.56)
check("1234,56 -> 1234.56",    _parse("1234,56",       "currency"), 1234.56)
check("59.674.856,62 -> ...",  _parse("59.674.856,62", "currency"), 59674856.62)
check("59,674,856.62 -> ...",  _parse("59,674,856.62", "currency"), 59674856.62)

print()
print("=== Sonderwerte ===")

empty_result = _numeric_compensation_series(pd.Series([""]),   default=0.0).iloc[0]
check("Leerstring -> 0.0",    empty_result, 0.0)
none_result  = _numeric_compensation_series(pd.Series([None]), default=0.0).iloc[0]
check("None -> 0.0",          none_result,  0.0)

print()
print(f"=== Ergebnis: {PASS} PASS / {FAIL} FAIL ===")
if FAIL > 0:
    sys.exit(1)
