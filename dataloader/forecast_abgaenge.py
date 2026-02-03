"""
Forecast-Modul: Negative Fluktuation (Abgaenge).

Prognostiziert Abgaenge und kapazitaetswirksame negative Fluktuation
vom IST-Stichtag bis zum Forecast-Ende. Liefert Ergebnisse je Periode
fuer Headcount, MAK und Abgaenge nach Ursache.

Abgangskategorien (Prioritaet absteigend):
  1. ATZ (Altersteilzeit) - deterministisch aus ATZ.xlsx
  2. Rente - probabilistisch nach Alter
  3. Kuendigung - stochastisch nach Alter x Tenure Matrix
  4. Ruhend-Dynamik - Rueckkehr und neue Faelle

Keine Streamlit-Abhaengigkeit: Modul ist rein pandas/numpy-basiert.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set

import numpy as np
import pandas as pd


# =============================================================================
# PARAMETER
# =============================================================================

# Kuendigungswahrscheinlichkeit p.a. nach Alter x Tenure.
# Schluessel: (Altersgruppe, Tenuregruppe) -> Rate
# Altersgruppen: "u30" (<30), "30_45" (30-44), "ue45" (>=45)
# Tenuregruppen: "u2" (<2J), "2_5" (2-5J), "ue5" (>5J)
DEFAULT_QUIT_RATE_MATRIX: Dict[Tuple[str, str], float] = {
    ("u30", "u2"):  0.12,
    ("u30", "2_5"): 0.08,
    ("u30", "ue5"): 0.05,
    ("30_45", "u2"):  0.08,
    ("30_45", "2_5"): 0.05,
    ("30_45", "ue5"): 0.03,
    ("ue45", "u2"):  0.05,
    ("ue45", "2_5"): 0.03,
    ("ue45", "ue5"): 0.02,
}


@dataclass
class ForecastParams:
    """
    Parameter fuer den Abgangs-Forecast.

    Alle Raten sind Jahresraten und werden intern auf die Periodenlaenge
    (Monat) umgerechnet.
    """
    # Rente
    rent_rate_65: float = 0.90       # Anteil >=65, die pro Jahr ausscheiden
    rent_rate_60_65: float = 0.10    # Anteil 60-64, Fruehverrentung p.a.

    # ATZ - neue Vertraege pro Jahr (fuer Personen ohne bestehenden ATZ-Vertrag)
    new_atz_cases_per_year: int = 5
    atz_eligible_age_min: int = 55   # Mindestalter fuer ATZ-Berechtigung
    atz_eligible_age_max: int = 60   # Hoechstalter fuer neuen ATZ-Start
    atz_ar_duration_months: int = 24 # Standarddauer Arbeitsphase
    atz_fr_duration_months: int = 24 # Standarddauer Freistellungsphase

    # Kuendigung
    quit_rate_base: float = 0.05     # Fallback-Kuendigungsquote p.a.
    quit_rate_matrix: Dict[Tuple[str, str], float] = field(
        default_factory=lambda: DEFAULT_QUIT_RATE_MATRIX.copy()
    )
    use_quit_matrix: bool = True     # True = Matrix, False = quit_rate_base

    # Ruhend
    ruhend_return_rate: float = 0.95         # Anteil, der aus Ruhend zurueckkehrt
    avg_ruhend_duration_months: int = 12     # Mittlere Abwesenheitsdauer
    new_ruhend_rate: float = 0.005           # Neue Ruhend-Faelle p.a. (Anteil am Bestand)

    # Allgemein
    random_seed: int = 42
    forecast_months: int = 60        # Forecast-Horizont (Default: 5 Jahre)


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

ID_PAD_LENGTH = 6


def _normalize_persnr(series: pd.Series) -> pd.Series:
    """Normalisiert PersNr zu String mit fuehrenden Nullen."""
    return series.apply(
        lambda x: str(int(x)).zfill(ID_PAD_LENGTH) if pd.notna(x) else pd.NA
    )


def _age_at(gebdatum: pd.Series, stichtag: pd.Timestamp) -> pd.Series:
    """Berechnet Alter in Jahren zum Stichtag."""
    return (stichtag - gebdatum).dt.days / 365.25


def _tenure_at(eintritt: pd.Series, stichtag: pd.Timestamp) -> pd.Series:
    """Berechnet Betriebszugehoerigkeit in Jahren zum Stichtag."""
    return (stichtag - eintritt).dt.days / 365.25


def _age_group(alter: float) -> str:
    """Ordnet Alter einer Altersgruppe zu (fuer Quit-Matrix)."""
    if alter < 30:
        return "u30"
    elif alter < 45:
        return "30_45"
    return "ue45"


def _tenure_group(tenure: float) -> str:
    """Ordnet Tenure einer Gruppe zu (fuer Quit-Matrix)."""
    if tenure < 2:
        return "u2"
    elif tenure < 5:
        return "2_5"
    return "ue5"


def _annual_to_monthly(rate: float) -> float:
    """Wandelt Jahresrate in Monatsrate um (1-(1-r)^(1/12))."""
    if rate <= 0:
        return 0.0
    if rate >= 1:
        return 1.0
    return 1.0 - (1.0 - rate) ** (1.0 / 12.0)


# =============================================================================
# DATEN-VORBEREITUNG
# =============================================================================

def prepare_workforce(
    df_ma: pd.DataFrame,
    df_atz: pd.DataFrame,
    stichtag: pd.Timestamp,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Bereitet Mitarbeiter- und ATZ-Daten fuer den Forecast auf.

    Returns:
        (workforce_df, atz_df) - bereinigte DataFrames
    """
    # --- Mitarbeiter ---
    wf = df_ma.copy()
    wf["PersNr"] = _normalize_persnr(wf["PersNr"])
    wf["GebDatum"] = pd.to_datetime(wf["GebDatum"], errors="coerce")
    wf["Eintritt"] = pd.to_datetime(wf["Eintritt"], errors="coerce")
    wf["Austritt"] = pd.to_datetime(wf["Austritt"], errors="coerce")

    # Austritt 9999-12-31 als offen interpretieren
    wf.loc[wf["Austritt"].dt.year == 9999, "Austritt"] = pd.NaT

    wf["BsGrd"] = pd.to_numeric(wf["BsGrd"], errors="coerce").fillna(0)
    wf["Alter"] = _age_at(wf["GebDatum"], stichtag)
    wf["Tenure"] = _tenure_at(wf["Eintritt"], stichtag)

    # Status-Flags
    wf["ist_ruhend"] = (
        wf["Status kundenindividuell"]
        .astype(str).str.strip()
        .str.lower()
        .str.contains("ruhend", na=False)
    )

    # --- ATZ ---
    atz = df_atz.copy()
    atz["PersNr"] = _normalize_persnr(atz["PersNr"])
    atz["Phase"] = atz["Phase"].astype(str).str.strip().str.upper()
    atz["Beginn"] = pd.to_datetime(atz["Beginn"], errors="coerce")
    atz["Ende"] = pd.to_datetime(atz["Ende"], errors="coerce")
    atz["Ende ATZ Vertrag"] = pd.to_datetime(
        atz["Ende ATZ Vertrag"], errors="coerce"
    )

    # Dedupliziere: bei Split-AR (2 AR-Zeilen) nehme min(Beginn), max(Ende)
    # pro PersNr + Phase
    atz_agg = (
        atz.groupby(["PersNr", "Phase"])
        .agg(
            Beginn=("Beginn", "min"),
            Ende=("Ende", "max"),
            Ende_ATZ_Vertrag=("Ende ATZ Vertrag", "min"),
        )
        .reset_index()
    )

    # ATZ-Personen-Set und aktuelle Phase bestimmen
    atz_persnr = set(atz_agg["PersNr"])
    atz_fr_aktuell = set(
        atz_agg[
            (atz_agg["Phase"] == "FR")
            & (atz_agg["Beginn"] <= stichtag)
            & (atz_agg["Ende"] >= stichtag)
        ]["PersNr"]
    )
    atz_ar_aktuell = set(
        atz_agg[
            (atz_agg["Phase"] == "AR")
            & (atz_agg["Beginn"] <= stichtag)
            & (atz_agg["Ende"] >= stichtag)
        ]["PersNr"]
    )

    wf["ist_atz"] = wf["PersNr"].isin(atz_persnr)
    wf["ist_atz_fr"] = wf["PersNr"].isin(atz_fr_aktuell)
    wf["ist_atz_ar"] = wf["PersNr"].isin(atz_ar_aktuell)

    # MAK berechnen (Ist-Stand)
    wf["MAK"] = wf.apply(_calc_mak_row, axis=1)

    # Im Bestand: alle die noch nicht ausgeschieden sind
    wf["im_bestand"] = True

    return wf, atz_agg


def _calc_mak_row(row) -> float:
    """Berechnet MAK fuer eine Zeile."""
    if row.get("ist_ruhend", False):
        return 0.0
    if row.get("ist_atz_fr", False):
        return 0.0
    return row.get("BsGrd", 0) / 100.0


# =============================================================================
# ATZ-EREIGNISSE
# =============================================================================

def get_atz_events(
    atz_df: pd.DataFrame,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
) -> Dict[str, List[str]]:
    """
    Ermittelt ATZ-Ereignisse in einer Periode.

    Returns:
        Dict mit:
        - 'ar_to_fr': PersNr-Liste der AR->FR Uebergaenge (MAK faellt auf 0)
        - 'exits': PersNr-Liste der FR-Enden (Headcount faellt)
    """
    events: Dict[str, List[str]] = {"ar_to_fr": [], "exits": []}

    # AR -> FR: FR-Beginn liegt in der Periode
    fr_rows = atz_df[atz_df["Phase"] == "FR"]
    ar_to_fr = fr_rows[
        (fr_rows["Beginn"] >= period_start) & (fr_rows["Beginn"] <= period_end)
    ]
    events["ar_to_fr"] = list(ar_to_fr["PersNr"].unique())

    # FR-Ende: echter Austritt
    fr_exits = fr_rows[
        (fr_rows["Ende"] >= period_start) & (fr_rows["Ende"] <= period_end)
    ]
    events["exits"] = list(fr_exits["PersNr"].unique())

    return events


# =============================================================================
# RENTEN-ABGAENGE
# =============================================================================

def get_retirement_exits(
    wf: pd.DataFrame,
    period_stichtag: pd.Timestamp,
    params: ForecastParams,
    excluded_persnr: Set[str],
    rng: np.random.Generator,
) -> List[str]:
    """
    Bestimmt Renten-Abgaenge in einer Periode.

    Exkludiert Personen mit ATZ-Vertrag (Prioritaetsregel).

    Args:
        wf: Workforce DataFrame (nur im_bestand=True)
        period_stichtag: Stichtag der Periode
        params: Forecast-Parameter
        excluded_persnr: PersNr die bereits durch ATZ ausscheiden
        rng: Numpy Random Generator

    Returns:
        Liste der PersNr die in Rente gehen
    """
    active = wf[
        wf["im_bestand"]
        & ~wf["PersNr"].isin(excluded_persnr)
        & ~wf["ist_atz"]  # ATZ hat Vorrang
    ].copy()

    if active.empty:
        return []

    active["Alter"] = _age_at(active["GebDatum"], period_stichtag)

    exits = []

    # Gruppe 1: Alter >= 65
    grp_65 = active[active["Alter"] >= 65]
    if not grp_65.empty:
        monthly_rate = _annual_to_monthly(params.rent_rate_65)
        draws = rng.random(len(grp_65))
        exits.extend(grp_65.loc[draws < monthly_rate, "PersNr"].tolist())

    # Gruppe 2: Alter 60-64
    grp_60_64 = active[(active["Alter"] >= 60) & (active["Alter"] < 65)]
    if not grp_60_64.empty:
        monthly_rate = _annual_to_monthly(params.rent_rate_60_65)
        draws = rng.random(len(grp_60_64))
        exits.extend(grp_60_64.loc[draws < monthly_rate, "PersNr"].tolist())

    return exits


# =============================================================================
# KUENDIGUNGEN
# =============================================================================

def get_quit_exits(
    wf: pd.DataFrame,
    period_stichtag: pd.Timestamp,
    params: ForecastParams,
    excluded_persnr: Set[str],
    rng: np.random.Generator,
) -> List[str]:
    """
    Bestimmt Kuendigungen in einer Periode (stochastisch, seeded).

    Exkludiert Personen mit ATZ, Rente und Azubis.

    Returns:
        Liste der PersNr die kuendigen
    """
    active = wf[
        wf["im_bestand"]
        & ~wf["PersNr"].isin(excluded_persnr)
        & ~wf["ist_atz"]
        & ~wf["ist_ruhend"]
    ].copy()

    # Azubis ausschliessen
    if "MitarbGruppenbez." in active.columns:
        active = active[active["MitarbGruppenbez."] != "Auszubildende"]

    if active.empty:
        return []

    active["Alter"] = _age_at(active["GebDatum"], period_stichtag)
    active["Tenure"] = _tenure_at(active["Eintritt"], period_stichtag)

    if params.use_quit_matrix:
        active["_ag"] = active["Alter"].apply(_age_group)
        active["_tg"] = active["Tenure"].apply(_tenure_group)
        active["_quit_rate_annual"] = active.apply(
            lambda r: params.quit_rate_matrix.get(
                (r["_ag"], r["_tg"]), params.quit_rate_base
            ),
            axis=1,
        )
    else:
        active["_quit_rate_annual"] = params.quit_rate_base

    active["_quit_rate_monthly"] = active["_quit_rate_annual"].apply(
        _annual_to_monthly
    )

    draws = rng.random(len(active))
    exits = active.loc[
        draws < active["_quit_rate_monthly"].values, "PersNr"
    ].tolist()

    return exits


# =============================================================================
# RUHEND-DYNAMIK
# =============================================================================

def process_ruhend(
    wf: pd.DataFrame,
    period_stichtag: pd.Timestamp,
    params: ForecastParams,
    ruhend_tracker: Dict[str, int],
    rng: np.random.Generator,
) -> Tuple[List[str], List[str], Dict[str, int]]:
    """
    Verarbeitet Ruhend-Status: Rueckkehrer und neue Faelle.

    Args:
        wf: Workforce DataFrame
        period_stichtag: Stichtag
        params: Parameter
        ruhend_tracker: Dict PersNr -> verbleibende Monate Ruhend
        rng: RNG

    Returns:
        (rueckkehrer, neue_ruhend, aktualisierter_tracker)
    """
    tracker = ruhend_tracker.copy()
    rueckkehrer = []
    neue_ruhend = []

    # Bestehende Ruhend-Faelle: Zaehler runterzaehlen
    for persnr in list(tracker.keys()):
        tracker[persnr] -= 1
        if tracker[persnr] <= 0:
            # Rueckkehr oder dauerhafter Abgang
            if rng.random() < params.ruhend_return_rate:
                rueckkehrer.append(persnr)
            # else: bleibt ruhend (wird nicht entfernt, Tracker auf 0)
            del tracker[persnr]

    # Neue Ruhend-Faelle
    active = wf[
        wf["im_bestand"]
        & ~wf["ist_ruhend"]
        & ~wf["ist_atz"]
        & ~wf["PersNr"].isin(tracker)
    ]
    if "MitarbGruppenbez." in active.columns:
        active = active[active["MitarbGruppenbez."] != "Auszubildende"]

    if not active.empty:
        monthly_new_rate = _annual_to_monthly(params.new_ruhend_rate)
        draws = rng.random(len(active))
        new_persnr = active.loc[draws < monthly_new_rate, "PersNr"].tolist()
        for p in new_persnr:
            tracker[p] = params.avg_ruhend_duration_months
            neue_ruhend.append(p)

    return rueckkehrer, neue_ruhend, tracker


# =============================================================================
# NEUE ATZ-VERTRAEGE (synthetisch)
# =============================================================================

def generate_new_atz_contracts(
    wf: pd.DataFrame,
    atz_df: pd.DataFrame,
    period_start: pd.Timestamp,
    params: ForecastParams,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Erzeugt synthetische neue ATZ-Vertraege fuer die Periode.

    Returns:
        DataFrame mit neuen ATZ-Zeilen (AR + FR) die an atz_df angehaengt
        werden koennen. Leerer DataFrame wenn keine neuen Vertraege.
    """
    monthly_cases = params.new_atz_cases_per_year / 12.0

    # Poisson-verteilte Anzahl neuer Faelle
    n_new = rng.poisson(monthly_cases)
    if n_new == 0:
        return pd.DataFrame(columns=atz_df.columns)

    atz_persnr = set(atz_df["PersNr"])

    eligible = wf[
        wf["im_bestand"]
        & ~wf["ist_atz"]
        & ~wf["ist_ruhend"]
        & (wf["Alter"] >= params.atz_eligible_age_min)
        & (wf["Alter"] <= params.atz_eligible_age_max)
    ]
    if "MitarbGruppenbez." in eligible.columns:
        eligible = eligible[eligible["MitarbGruppenbez."] != "Auszubildende"]

    # Bereits in ATZ ausschliessen
    eligible = eligible[~eligible["PersNr"].isin(atz_persnr)]

    if eligible.empty or n_new == 0:
        return pd.DataFrame(columns=atz_df.columns)

    n_new = min(n_new, len(eligible))
    selected = eligible.sample(n=n_new, random_state=rng.integers(0, 2**31))

    new_rows = []
    for _, person in selected.iterrows():
        ar_start = period_start
        ar_end = ar_start + pd.DateOffset(months=params.atz_ar_duration_months) - pd.Timedelta(days=1)
        fr_start = ar_end + pd.Timedelta(days=1)
        fr_end = fr_start + pd.DateOffset(months=params.atz_fr_duration_months) - pd.Timedelta(days=1)

        new_rows.append({
            "PersNr": person["PersNr"],
            "Phase": "AR",
            "Beginn": ar_start,
            "Ende": ar_end,
            "Ende_ATZ_Vertrag": fr_end,
        })
        new_rows.append({
            "PersNr": person["PersNr"],
            "Phase": "FR",
            "Beginn": fr_start,
            "Ende": fr_end,
            "Ende_ATZ_Vertrag": fr_end,
        })

    return pd.DataFrame(new_rows)


# =============================================================================
# HAUPTFUNKTION: FORECAST-SCHLEIFE
# =============================================================================

def run_forecast(
    df_ma: pd.DataFrame,
    df_atz: pd.DataFrame,
    params: Optional[ForecastParams] = None,
    stichtag: Optional[pd.Timestamp] = None,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Fuehrt den Abgangs-Forecast durch.

    Args:
        df_ma: Mitarbeiter-DataFrame (Rohdaten aus Mitarbeiter.xlsx)
        df_atz: ATZ-DataFrame (Rohdaten aus ATZ.xlsx)
        params: Forecast-Parameter (Default: ForecastParams())
        stichtag: IST-Stichtag (Default: heute)

    Returns:
        Tuple aus:
        - forecast_abgaenge: DataFrame mit Ergebnissen je Periode
        - meta: Dict mit Metadaten (Parameter, Warnungen, Zusammenfassung)
    """
    if params is None:
        params = ForecastParams()
    if stichtag is None:
        stichtag = pd.Timestamp.today().normalize()

    rng = np.random.default_rng(params.random_seed)

    # Vorbereitung
    wf, atz_agg = prepare_workforce(df_ma, df_atz, stichtag)

    # Initialer Bestand
    initial_headcount = int(wf["im_bestand"].sum())
    initial_mak = float(wf.loc[wf["im_bestand"], "MAK"].sum())

    # Ruhend-Tracker: bestehende Ruhend-Personen mit geschaetzter Restdauer.
    # Streue die Restdauer gleichmaessig ueber [1, avg_duration], damit nicht
    # alle gleichzeitig zurueckkehren.
    ruhend_tracker: Dict[str, int] = {}
    ruhend_persnr_list = wf.loc[
        wf["ist_ruhend"] & wf["im_bestand"], "PersNr"
    ].tolist()
    if ruhend_persnr_list:
        max_rest = max(1, params.avg_ruhend_duration_months)
        rest_values = rng.integers(1, max_rest + 1, size=len(ruhend_persnr_list))
        for persnr, rest in zip(ruhend_persnr_list, rest_values):
            ruhend_tracker[persnr] = int(rest)

    # Perioden generieren (monatlich, ab 1. des Folgemonats)
    first_period = (stichtag.replace(day=1) + pd.DateOffset(months=1))
    periods = pd.date_range(
        start=first_period,
        periods=params.forecast_months,
        freq="MS",
    )

    results = []
    warnings = []

    # Arbeitskopie ATZ (wird um neue Vertraege ergaenzt)
    atz_live = atz_agg.copy()

    for i, period_start in enumerate(periods):
        period_end = period_start + pd.DateOffset(months=1) - pd.Timedelta(days=1)
        period_stichtag = period_end

        # Bestand zu Beginn der Periode
        hc_start = int(wf["im_bestand"].sum())
        mak_start = float(wf.loc[wf["im_bestand"], "MAK"].sum())

        # Tracking-Sets fuer Prioritaetsregeln
        already_exited: Set[str] = set()

        # --- 1. ATZ-Ereignisse (deterministisch, hoechste Prioritaet) ---
        atz_events = get_atz_events(atz_live, period_start, period_end)

        # AR -> FR: MAK faellt auf 0, Headcount bleibt
        ar_to_fr = atz_events["ar_to_fr"]
        mak_loss_ar_to_fr = 0.0
        for persnr in ar_to_fr:
            mask = (wf["PersNr"] == persnr) & wf["im_bestand"]
            mak_before = float(wf.loc[mask, "MAK"].sum())
            wf.loc[mask, "ist_atz_fr"] = True
            wf.loc[mask, "ist_atz_ar"] = False
            wf.loc[mask, "MAK"] = 0.0
            mak_loss_ar_to_fr += mak_before

        # FR-Ende: echter Austritt
        atz_exits = atz_events["exits"]
        mak_loss_atz_exits = 0.0
        for persnr in atz_exits:
            mask = (wf["PersNr"] == persnr) & wf["im_bestand"]
            mak_loss_atz_exits += float(wf.loc[mask, "MAK"].sum())
            wf.loc[mask, "im_bestand"] = False
            wf.loc[mask, "MAK"] = 0.0
            already_exited.add(persnr)

        # --- 2. Renten-Abgaenge (unter Beachtung ATZ-Prioritaet) ---
        retirement_exits = get_retirement_exits(
            wf, period_stichtag, params, already_exited, rng
        )
        mak_loss_retirement = 0.0
        for persnr in retirement_exits:
            mask = (wf["PersNr"] == persnr) & wf["im_bestand"]
            mak_loss_retirement += float(wf.loc[mask, "MAK"].sum())
            wf.loc[mask, "im_bestand"] = False
            wf.loc[mask, "MAK"] = 0.0
            already_exited.add(persnr)

        # --- 3. Kuendigungen (unter Beachtung ATZ + Rente) ---
        quit_exits = get_quit_exits(
            wf, period_stichtag, params, already_exited, rng
        )
        mak_loss_quit = 0.0
        for persnr in quit_exits:
            mask = (wf["PersNr"] == persnr) & wf["im_bestand"]
            mak_loss_quit += float(wf.loc[mask, "MAK"].sum())
            wf.loc[mask, "im_bestand"] = False
            wf.loc[mask, "MAK"] = 0.0
            already_exited.add(persnr)

        # --- 4. Ruhend-Dynamik ---
        rueckkehrer, neue_ruhend, ruhend_tracker = process_ruhend(
            wf, period_stichtag, params, ruhend_tracker, rng
        )

        mak_gain_ruhend_return = 0.0
        for persnr in rueckkehrer:
            mask = (wf["PersNr"] == persnr) & wf["im_bestand"]
            if mask.any():
                wf.loc[mask, "ist_ruhend"] = False
                wf.loc[mask, "MAK"] = wf.loc[mask, "BsGrd"] / 100.0
                mak_gain_ruhend_return += float(wf.loc[mask, "MAK"].sum())

        mak_loss_new_ruhend = 0.0
        for persnr in neue_ruhend:
            mask = (wf["PersNr"] == persnr) & wf["im_bestand"]
            if mask.any():
                mak_before = float(wf.loc[mask, "MAK"].sum())
                wf.loc[mask, "ist_ruhend"] = True
                wf.loc[mask, "MAK"] = 0.0
                mak_loss_new_ruhend += mak_before

        # --- 5. Neue ATZ-Vertraege (synthetisch) ---
        new_atz = generate_new_atz_contracts(
            wf, atz_live, period_start, params, rng
        )
        if not new_atz.empty:
            atz_live = pd.concat([atz_live, new_atz], ignore_index=True)
            # Markiere neue ATZ-Personen
            new_atz_persnr = set(new_atz["PersNr"])
            wf.loc[wf["PersNr"].isin(new_atz_persnr), "ist_atz"] = True
            wf.loc[wf["PersNr"].isin(new_atz_persnr), "ist_atz_ar"] = True

        # --- 6. Bestand aktualisieren ---
        hc_end = int(wf["im_bestand"].sum())
        mak_end = float(wf.loc[wf["im_bestand"], "MAK"].sum())

        # Echte Austritte (Headcount-relevant): ATZ-Ende + Rente + Kuendigung
        exits_total = len(atz_exits) + len(retirement_exits) + len(quit_exits)
        headcount_loss = hc_start - hc_end

        # MAK-Verlust Gesamt (inkl. AR->FR und neue Ruhend, abzgl. Rueckkehrer)
        mak_loss_ruhend_net = mak_loss_new_ruhend - mak_gain_ruhend_return
        mak_loss_exits = mak_loss_atz_exits + mak_loss_retirement + mak_loss_quit
        mak_loss_total = mak_loss_ar_to_fr + mak_loss_exits + mak_loss_ruhend_net

        # Abgangsquote: Abgaenge / Durchschn. Bestand
        avg_bestand = (hc_start + hc_end) / 2.0
        abgangsquote = exits_total / avg_bestand if avg_bestand > 0 else 0.0

        # --- Qualitaetschecks ---
        exits_sum = len(atz_exits) + len(retirement_exits) + len(quit_exits)
        if exits_total != exits_sum:
            warnings.append(
                f"Periode {period_start:%Y-%m}: "
                f"exits_total ({exits_total}) != Summe ({exits_sum})"
            )

        mak_sum = mak_loss_ar_to_fr + mak_loss_exits + mak_loss_ruhend_net
        if abs(mak_loss_total - mak_sum) > 0.01:
            warnings.append(
                f"Periode {period_start:%Y-%m}: "
                f"MAK-Inkonsistenz: {mak_loss_total:.2f} vs {mak_sum:.2f}"
            )

        results.append({
            "period_start": period_start,
            "period_end": period_end,
            # Abgaenge
            "exits_total": exits_total,
            "exits_atz_end": len(atz_exits),
            "exits_retirement": len(retirement_exits),
            "exits_quit": len(quit_exits),
            "exits_other": 0,  # Erweiterbar
            # ATZ-Uebergaenge (kein Exit, aber MAK-Verlust)
            "atz_ar_to_fr": len(ar_to_fr),
            # MAK-Verluste
            "mak_loss_total": round(mak_loss_total, 2),
            "mak_loss_atz_ar_to_fr": round(mak_loss_ar_to_fr, 2),
            "mak_loss_exits": round(mak_loss_exits, 2),
            "mak_loss_ruhend": round(mak_loss_ruhend_net, 2),
            # Ruhend-Details
            "ruhend_rueckkehrer": len(rueckkehrer),
            "ruhend_neue": len(neue_ruhend),
            # Neue ATZ-Vertraege
            "new_atz_contracts": len(new_atz) // 2 if not new_atz.empty else 0,
            # Headcount
            "headcount_loss_total": headcount_loss,
            "headcount_start": hc_start,
            "headcount_end": hc_end,
            # MAK
            "mak_start": round(mak_start, 2),
            "mak_end": round(mak_end, 2),
            # Quote
            "abgangsquote": round(abgangsquote, 4),
        })

    forecast_df = pd.DataFrame(results)

    # --- Metadaten ---
    meta = {
        "stichtag": stichtag,
        "forecast_months": params.forecast_months,
        "initial_headcount": initial_headcount,
        "initial_mak": round(initial_mak, 2),
        "final_headcount": int(wf["im_bestand"].sum()),
        "final_mak": round(float(wf.loc[wf["im_bestand"], "MAK"].sum()), 2),
        "total_exits": int(forecast_df["exits_total"].sum()),
        "total_exits_by_reason": {
            "atz_end": int(forecast_df["exits_atz_end"].sum()),
            "retirement": int(forecast_df["exits_retirement"].sum()),
            "quit": int(forecast_df["exits_quit"].sum()),
            "other": int(forecast_df["exits_other"].sum()),
        },
        "total_mak_loss": round(float(forecast_df["mak_loss_total"].sum()), 2),
        "total_ar_to_fr": int(forecast_df["atz_ar_to_fr"].sum()),
        "warnings": warnings,
        "params": {
            "rent_rate_65": params.rent_rate_65,
            "rent_rate_60_65": params.rent_rate_60_65,
            "new_atz_cases_per_year": params.new_atz_cases_per_year,
            "quit_rate_base": params.quit_rate_base,
            "use_quit_matrix": params.use_quit_matrix,
            "ruhend_return_rate": params.ruhend_return_rate,
            "avg_ruhend_duration_months": params.avg_ruhend_duration_months,
            "new_ruhend_rate": params.new_ruhend_rate,
            "random_seed": params.random_seed,
            "forecast_months": params.forecast_months,
        },
    }

    return forecast_df, meta


# =============================================================================
# CONVENIENCE: DIREKTER AUFRUF MIT DATEIPFADEN
# =============================================================================

def run_forecast_from_files(
    mitarbeiter_path: str,
    atz_path: str,
    params: Optional[ForecastParams] = None,
    stichtag: Optional[pd.Timestamp] = None,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Laedt Daten aus Excel-Dateien und fuehrt den Forecast durch.

    Args:
        mitarbeiter_path: Pfad zu Mitarbeiter.xlsx
        atz_path: Pfad zu ATZ.xlsx
        params: Forecast-Parameter
        stichtag: IST-Stichtag

    Returns:
        Tuple aus (forecast_df, meta)
    """
    df_ma = pd.read_excel(mitarbeiter_path)
    df_atz = pd.read_excel(
        atz_path, parse_dates=["Beginn", "Ende", "Ende ATZ Vertrag"]
    )
    return run_forecast(df_ma, df_atz, params, stichtag)


# =============================================================================
# CLI-TEST
# =============================================================================

if __name__ == "__main__":
    import os
    import sys

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(os.path.dirname(base), "Original-Daten")

    ma_path = os.path.join(data_dir, "Mitarbeiter.xlsx")
    atz_path = os.path.join(data_dir, "ATZ.xlsx")

    if not os.path.exists(ma_path):
        print(f"Mitarbeiter.xlsx nicht gefunden: {ma_path}")
        sys.exit(1)

    print("=" * 70)
    print("FORECAST ABGAENGE - Testlauf")
    print("=" * 70)

    params = ForecastParams(forecast_months=60)
    forecast_df, meta = run_forecast_from_files(ma_path, atz_path, params)

    print(f"\nStichtag:           {meta['stichtag']:%Y-%m-%d}")
    print(f"Horizont:           {meta['forecast_months']} Monate")
    print(f"Initial Headcount:  {meta['initial_headcount']}")
    print(f"Initial MAK:        {meta['initial_mak']:.1f}")
    print(f"Final Headcount:    {meta['final_headcount']}")
    print(f"Final MAK:          {meta['final_mak']:.1f}")
    print(f"\nAbgaenge gesamt:    {meta['total_exits']}")

    for reason, count in meta["total_exits_by_reason"].items():
        print(f"  {reason:.<20} {count}")

    print(f"\nMAK-Verlust gesamt: {meta['total_mak_loss']:.1f}")
    print(f"AR->FR Uebergaenge: {meta['total_ar_to_fr']}")

    if meta["warnings"]:
        print(f"\nWarnungen ({len(meta['warnings'])}):")
        for w in meta["warnings"][:5]:
            print(f"  ! {w}")

    print("\n--- Erste 12 Perioden ---")
    cols = [
        "period_start", "exits_total", "exits_atz_end",
        "exits_retirement", "exits_quit", "atz_ar_to_fr",
        "mak_loss_total", "headcount_end", "mak_end",
    ]
    print(forecast_df[cols].head(12).to_string(index=False))
    print("=" * 70)
