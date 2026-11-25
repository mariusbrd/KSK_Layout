"""Readable Monte Carlo detail for one OE or OE cluster across annual slices."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def annual_band_export_tables(bands: pd.DataFrame, *, group_col: str,
                              group: str, x_col: str, unit: str,
                              ensemble: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use the plotted values unchanged in a think-cell friendly row matrix."""
    selected = bands.loc[bands[group_col].astype(str).eq(group)].sort_values(x_col)
    if selected.empty:
        raise ValueError("Für diese Organisation liegen keine Jahresscheiben vor.")
    positions = []
    for index, value in enumerate(selected[x_col]):
        if x_col == "Stichtag":
            date = pd.Timestamp(value)
            label = ("Start" if index == 0 and (date.month, date.day) != (12, 31)
                     else int(date.year) if (date.month, date.day) == (12, 31)
                     else date.date().isoformat())
        else:
            label = int(value)
        if label in positions:
            raise ValueError("Mehrere Jahresscheiben haben dieselbe Exportspalte.")
        positions.append(label)
    series = (
        ("Mittelwert", "mean"), ("Median (P50)", "median"),
        ("P10", "lower"), ("P25", "p25"),
        ("P75", "p75"), ("P90", "upper"),
        ("Referenzlauf (Lauf 1)" if ensemble is not None else "Referenzlauf", "Referenzlauf"),
    )
    matrix = pd.DataFrame([
        {"Datenreihe": label, **{position: float(value) for position, value in
                                   zip(positions, selected[column])}}
        for label, column in series
    ])
    samples = bands.attrs.get("run_samples", {})
    if ensemble is not None:
        from utils.monte_carlo_representative_runs import select_representative_runs

        chosen = select_representative_runs(ensemble)
        sample_lookup = {(str(period)[:10], str(sample_group)): values
                         for (period, sample_group), values in samples.items()}
        for label, key in (("Geringste Fluktuation", "best"),
                           ("Typischer Einzellauf", "typical"),
                           ("Höchste Fluktuation", "worst")):
            run_index = chosen[key]["index"]
            values = []
            for period in selected[x_col]:
                run_values = sample_lookup.get((str(period)[:10], group))
                if run_values is None or len(run_values) != ensemble["count"]:
                    raise ValueError("Jahreswerte des ausgewählten Einzellaufs fehlen")
                values.append(float(run_values[run_index]))
            matrix.loc[len(matrix)] = {"Datenreihe": f"{label} (Lauf {run_index + 1})",
                                       **dict(zip(positions, values))}
    selected_periods = {str(period)[:10] for period in selected[x_col]}
    counts = {len(values) for (period, sample_group), values in samples.items()
              if str(sample_group) == group and str(period)[:10] in selected_periods}
    metadata = pd.DataFrame([
        ("Auswahl", group), ("Ebene", group_col), ("Einheit", unit),
        ("Anzahl Läufe", next(iter(counts)) if len(counts) == 1 else "Siehe Simulationslauf"),
        ("Jahresscheiben", ", ".join(map(str, positions))),
        ("Mittelwert", "Arithmetischer Mittelwert aller gespeicherten Läufe"),
        ("P10/P25/Median/P75/P90", "Perzentile der einzelnen Läufe je Jahresscheibe"),
        ("Referenzlauf", "Erster gespeicherter Einzellauf, kein Mittelwert"),
        ("Start", "Nur enthalten, wenn ein Ausgangsstichtag in der Grafik vorliegt"),
    ], columns=["Merkmal", "Wert"])
    if ensemble is not None:
        for label, key in (("Geringste Fluktuation", "best"),
                           ("Typischer Einzellauf", "typical"),
                           ("Höchste Fluktuation", "worst")):
            index = chosen[key]["index"]
            pair = ensemble["seed_pairs"][index]
            metadata.loc[len(metadata)] = (label, f"Lauf {index + 1}; Seeds {pair[0]}/{pair[1]}")
    return matrix, metadata


def annual_band_export_bytes(bands: pd.DataFrame, *, group_col: str,
                             group: str, x_col: str, unit: str,
                             ensemble: dict | None = None) -> bytes:
    from io import BytesIO

    matrix, metadata = annual_band_export_tables(
        bands, group_col=group_col, group=group, x_col=x_col, unit=unit,
        ensemble=ensemble)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        matrix.to_excel(writer, sheet_name="01_Liniendiagramm", index=False)
        metadata.to_excel(writer, sheet_name="02_Informationen", index=False)
        worksheet = writer.sheets["01_Liniendiagramm"]
        worksheet.freeze_panes = "B2"
        worksheet.column_dimensions["A"].width = 25
        for column in worksheet.iter_cols(min_col=2, min_row=2):
            for cell in column:
                cell.number_format = "0.00"
    return output.getvalue()


def annual_band_bulk_export_bytes(bands: pd.DataFrame, *, group_col: str,
                                  x_col: str, unit: str, ensemble: dict, level: str,
                                  event_types: tuple[str, ...] | None = None) -> bytes:
    """Export every stored group plus a total built from complete seed paths."""
    from io import BytesIO
    import re

    from utils.monte_carlo_ensemble import _statistics
    from utils.monte_carlo_org_view import _distribution

    export_level = level
    count = ensemble["count"]
    metric = ("heads" if "Köpfe" in unit else "mak") if x_col == "Stichtag" else unit
    periods = sorted(bands[x_col].unique())
    samples = {}
    direct_total = {}
    if x_col == "Stichtag":
        source = ensemble["statistics"].get("departure_stock_samples") or []
        for date, row_level, raw_group, row_metric, values in source:
            if row_level == "Gesamt" and row_metric == metric:
                direct_total[str(date)[:10]] = [float(value) for value in values]
            if row_level != export_level:
                continue
            if row_metric != metric:
                continue
            group = ("Nicht zugeordnet" if row_level == "OE-Cluster" and raw_group in
                     {"Unclustered", "UNMAPPED", "Unmapped"} else str(raw_group))
            target = samples.setdefault((str(date)[:10], group), [0.0] * count)
            if len(values) != count:
                raise ValueError("Unvollständige Laufwerte im Gesamtexport")
            for index, value in enumerate(values):
                target[index] += float(value)
        # The initial snapshot is not always part of the stored annual samples.
        rate_sources = ensemble["statistics"].get("departure_rate_inputs_by_seed") or []
        if len(rate_sources) == count and periods:
            first = str(periods[0])[:10]
            if pd.Timestamp(first).strftime("%m-%d") != "12-31":
                for index, by_level in enumerate(rate_sources):
                    openings = by_level.get(export_level, {}).get("opening", {})
                    for raw_group, values in openings.items():
                        group = ("Nicht zugeordnet" if raw_group in
                                 {"Unclustered", "UNMAPPED", "Unmapped"} else str(raw_group))
                        samples.setdefault((first, group), [0.0] * count)[index] = float(
                            values.get(metric, 0.0))
    else:
        selected_types = set(event_types or ())
        for year, row_level, group, kind, row_metric, values in (
                ensemble["statistics"].get("annual_event_samples") or []):
            if row_level == "Gesamt" and row_metric == metric and kind in selected_types:
                target = direct_total.setdefault(str(year), [0.0] * count)
                for index, value in enumerate(values):
                    target[index] += float(value)
            if row_level != export_level or row_metric != metric or kind not in selected_types:
                continue
            target = samples.setdefault((str(year), str(group)), [0.0] * count)
            for index, value in enumerate(values):
                target[index] += float(value)
    groups = sorted(({group for _, group in samples} | set(bands[group_col].astype(str)))
                    - {"Gesamt"})
    if not groups:
        raise ValueError("Keine Organisationswerte für den Gesamtexport verfügbar")
    period_keys = [str(period)[:10] for period in periods]
    records = []
    total_samples = {}
    for period, period_key in zip(periods, period_keys):
        total = [0.0] * count
        for group in groups:
            values = samples.get((period_key, group), [0.0] * count)
            if len(values) != count:
                raise ValueError("Unvollständige Laufwerte im Gesamtexport")
            for index, value in enumerate(values):
                total[index] += value
            stats = _statistics(values, ensemble["band_level"])
            records.append({x_col: period, group_col: group, "Referenzlauf": values[0],
                            **stats, **_distribution(values)})
        total = direct_total.get(period_key, total)
        if len(total) != count:
            raise ValueError("Unvollständige Gesamtwerte im Gesamtexport")
        total_samples[(period_key, "Gesamt")] = total
        stats = _statistics(total, ensemble["band_level"])
        records.append({x_col: period, group_col: "Gesamt", "Referenzlauf": total[0],
                        **stats, **_distribution(total)})
    complete = pd.DataFrame(records)
    complete.attrs["run_samples"] = {**samples, **total_samples}
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        index_rows = []
        for number, group in enumerate(["Gesamt", *groups], 1):
            matrix, _ = annual_band_export_tables(
                complete, group_col=group_col, group=group, x_col=x_col,
                unit=unit, ensemble=ensemble)
            clean = re.sub(r'[:\\/?*\[\]]', "_", group)[:26]
            sheet = f"{number:02d}_{clean}"[:31]
            matrix.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            ws.freeze_panes = "B2"
            ws.column_dimensions["A"].width = 38
            for column in ws.iter_cols(min_col=2, min_row=2):
                for cell in column:
                    cell.number_format = "0.00"
            index_rows.append({"Blatt": sheet, "Organisation": group, "Einheit": unit})
        pd.DataFrame(index_rows).to_excel(writer, sheet_name="00_Inhalt", index=False)
    return output.getvalue()


def build_annual_band_figure(bands: pd.DataFrame, *, group_col: str,
                             group: str, x_col: str, unit: str) -> go.Figure:
    """Show the outer and inner empirical intervals without changing the mean."""
    selected = bands.loc[bands[group_col].astype(str).eq(group)].sort_values(x_col)
    if selected.empty:
        raise ValueError("Für diese Organisation liegen keine Jahresscheiben vor.")
    x = selected[x_col].tolist()
    low = selected["lower"].astype(float).tolist()
    high = selected["upper"].astype(float).tolist()
    mean = selected["mean"].astype(float).tolist()
    p25 = selected["p25"].astype(float).tolist()
    median = selected["median"].astype(float).tolist()
    p75 = selected["p75"].astype(float).tolist()
    reference = selected["Referenzlauf"].astype(float).tolist()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=high, mode="lines", line=dict(color="rgba(107,114,128,0.45)", width=1),
        name="P90", hovertemplate="%{x}<br>P90: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=low, mode="lines", fill="tonexty",
        fillcolor="rgba(107,114,128,0.14)",
        line=dict(color="rgba(107,114,128,0.45)", width=1),
        name="P10–P90", hovertemplate="%{x}<br>P10: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=p75, mode="lines", line=dict(color="rgba(75,85,99,0.6)", width=1),
        name="P75", hovertemplate="%{x}<br>P75: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=p25, mode="lines", fill="tonexty",
        fillcolor="rgba(75,85,99,0.3)",
        line=dict(color="rgba(75,85,99,0.6)", width=1),
        name="P25–P75", hovertemplate="%{x}<br>P25: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=median, mode="lines+markers", line=dict(color="#374151", width=2),
        marker=dict(size=6), name="Median (P50)",
        hovertemplate="%{x}<br>Median: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=mean, mode="lines+markers", line=dict(color="#1f77b4", width=3),
        marker=dict(size=8), name="Mittelwert",
        hovertemplate="%{x}<br>Mittelwert: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=reference, mode="lines+markers", line=dict(color="#9ca3af", dash="dash", width=2),
        marker=dict(size=6), name="Referenzlauf",
        hovertemplate="%{x}<br>Referenzlauf: %{y:,.2f}<extra></extra>"))
    if len(x) == 1:
        fig.add_trace(go.Scatter(
            x=[x[0], x[0]], y=[low[0], high[0]], mode="lines+markers",
            line=dict(color="#6b7280", width=5), marker=dict(size=8),
            name="P10–P90 am Stichtag", showlegend=False))
    fig.update_layout(
        height=340, margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title="Jahr" if x_col == "Jahr" else "Stichtag", yaxis_title=unit,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=-0.2))
    if x_col == "Jahr":
        fig.update_xaxes(tickmode="array", tickvals=x)
    return fig


def build_annual_box_figure(bands: pd.DataFrame, *, group_col: str,
                            group: str, x_col: str, x_value, unit: str) -> go.Figure:
    """Render actual saved run values for one year, including individual points."""
    samples = bands.attrs.get("run_samples", {}).get((x_value, group))
    if not samples:
        raise ValueError("Für diese Jahresscheibe sind keine Einzelwerte gespeichert.")
    fig = go.Figure(go.Box(
        y=samples, name=str(x_value), boxpoints="all", jitter=.3, pointpos=0,
        marker=dict(color="#6b7280", size=6), line=dict(color="#374151"),
        fillcolor="rgba(107,114,128,0.24)", boxmean=True, quartilemethod="linear",
        hovertemplate="Laufwert: %{y:,.2f}<extra></extra>"))
    fig.update_layout(
        height=340, margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title="Jahr" if x_col == "Jahr" else "Stichtag", yaxis_title=unit,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    return fig


def render_annual_band_detail(bands: pd.DataFrame, *, group_col: str,
                              x_col: str, unit: str, key: str,
                              ensemble: dict | None = None, level: str | None = None,
                              event_types: tuple[str, ...] | None = None) -> None:
    """Show every selected year's interval for one chosen organization."""
    if bands.empty:
        return
    with st.expander("Jahresscheiben je OE / OE-Cluster mit Band", expanded=True):
        groups = sorted(bands[group_col].astype(str).unique())
        group = st.selectbox("OE / OE-Cluster auswählen", groups, key=key)
        mode = st.radio("Darstellung", ["Jahresverlauf", "Verteilung je Jahr"],
                        horizontal=True, key=f"{key}_mode")
        if mode == "Verteilung je Jahr":
            periods = sorted(bands.loc[bands[group_col].astype(str).eq(group), x_col].unique())
            period = st.selectbox("Jahresscheibe auswählen", periods, index=len(periods) - 1,
                                  key=f"{key}_period")
            fig = build_annual_box_figure(
                bands, group_col=group_col, group=group, x_col=x_col,
                x_value=period, unit=unit)
            st.caption("Box: P25–P75 und Median; Punkte: einzelne Simulationsläufe. Bei wenigen Läufen sind Quantile nur grobe Orientierung.")
        else:
            fig = build_annual_band_figure(
                bands, group_col=group_col, group=group, x_col=x_col, unit=unit)
            st.caption("Hellgrau: P10–P90. Dunkelgrau: P25–P75. Linien: Median, Mittelwert und Referenzlauf.")
        st.plotly_chart(fig, use_container_width=True, key=f"{key}_chart")
        if mode == "Jahresverlauf":
            from components.ui_compat import lazy_excel_download_button_compat

            lazy_excel_download_button_compat(
                label="Alle OEs / OE-Cluster + Gesamt für Excel / think-cell exportieren",
                data_builder=lambda: annual_band_bulk_export_bytes(
                    bands, group_col=group_col, x_col=x_col, unit=unit,
                    ensemble=ensemble, level=level or group_col,
                    event_types=event_types),
                file_name=f"Jahresverlauf_Alle_{(level or group_col).replace('-', '_')}_{unit.replace('-', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"{key}_thinkcell_export",
                fingerprint=(key, unit, level, event_types, id(ensemble),
                             tuple(bands[x_col].astype(str))),
            )
        display = bands.loc[bands[group_col].astype(str).eq(group)].rename(
            columns={"mean": "Mittelwert", "lower": "P10", "upper": "P90",
                     "p25": "P25", "median": "Median (P50)", "p75": "P75"})
        st.dataframe(display, hide_index=True, use_container_width=True)
