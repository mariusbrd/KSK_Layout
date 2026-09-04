from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from utils.compact_page_loader import clear_compact_page_module_cache, load_compact_page_module


def _load_page_module(filename_suffix: str, module_name: str):
    page_path = next((ROOT / "pages").glob(f"*_{filename_suffix}.py"))
    spec = importlib.util.spec_from_file_location(module_name, page_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _NoopContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _CaptureStreamlit:
    def __init__(self):
        self.figures = []
        self.infos = []
        self.captions = []

    def columns(self, spec):
        return [_NoopContext() for _ in spec]

    def plotly_chart(self, fig, **_kwargs):
        self.figures.append(fig)

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def caption(self, message, **_kwargs):
        self.captions.append(message)

    def divider(self):
        return None

    def subheader(self, *_args, **_kwargs):
        return None


class _FakeCompact:
    _EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def __init__(self):
        self.exports = []

    def format_number(self, value, decimals=0):
        return f"{float(value):.{decimals}f}"

    def format_currency(self, value):
        return f"{float(value):.2f} EUR"

    def format_percent(self, value):
        return f"{float(value) * 100:.1f}%"

    def create_breakdown_table(self, df, dimension_col, value_col):
        work_df = df.copy()
        if "Is_Vacant" in work_df.columns:
            work_df = work_df[work_df["Is_Vacant"] != True].copy()

        grouped = (
            work_df.groupby(dimension_col, observed=True)[value_col]
            .sum()
            .reset_index(name="IST")
        )
        total = float(grouped["IST"].sum()) if not grouped.empty else 0.0
        grouped["Anteil"] = grouped["IST"].apply(lambda value: float(value) / total if total else 0.0)
        return grouped.sort_values(["IST", dimension_col], ascending=[False, True]).reset_index(drop=True)

    def format_dataframe_for_display(self, df, value_type):
        display_df = df.copy()
        value_cols = [col for col in ("IST", "Simulation", "MAK") if col in display_df.columns]
        for col in value_cols:
            display_df[col] = display_df[col].apply(lambda value: self.format_number(value, 1))
        if "Anteil" in display_df.columns:
            display_df["Anteil"] = display_df["Anteil"].apply(self.format_percent)
        return display_df

    def export_to_excel(self, df, **kwargs):
        self.exports.append({"df": df.copy(), "kwargs": kwargs})
        return b"xlsx"


def _capture_render_helpers(monkeypatch, module):
    capture_st = _CaptureStreamlit()
    dataframes = []
    downloads = []

    monkeypatch.setattr(module, "st", capture_st)
    monkeypatch.setattr(module, "dataframe_compat", lambda data, **kwargs: dataframes.append({"data": data.copy(), "kwargs": kwargs}))
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: downloads.append(kwargs), raising=False)
    monkeypatch.setattr(
        module,
        "lazy_excel_download_button_compat",
        lambda **kwargs: downloads.append({**kwargs, "data": kwargs["data_builder"]()}),
        raising=False,
    )
    return capture_st, dataframes, downloads


def _analysis_snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "PersNr": "1",
                "Personalnummer": "1",
                "Is_Vacant": False,
                "Organisationseinheit": "OE A",
                "Jobfamily": "JF A",
                "Geschlecht": "w",
                "Alterskohorte": "30-39",
                "Beschaeftigungsstatus": "Aktiv",
                "Beschäftigungsstatus": "Aktiv",
                "TrfGr": "E9A",
                "MAK_Reporting": 1.0,
                "EUR_Reporting": 100.0,
                "Headcount": 1,
            },
            {
                "PersNr": "2",
                "Personalnummer": "2",
                "Is_Vacant": False,
                "Organisationseinheit": "OE A",
                "Jobfamily": "JF A",
                "Geschlecht": "m",
                "Alterskohorte": "40-49",
                "Beschaeftigungsstatus": "Aktiv",
                "Beschäftigungsstatus": "Aktiv",
                "TrfGr": "E10",
                "MAK_Reporting": 0.5,
                "EUR_Reporting": 50.0,
                "Headcount": 1,
            },
            {
                "PersNr": "3",
                "Personalnummer": "3",
                "Is_Vacant": False,
                "Organisationseinheit": "OE B",
                "Jobfamily": "JF B",
                "Geschlecht": "w",
                "Alterskohorte": "50-59",
                "Beschaeftigungsstatus": "Aktiv",
                "Beschäftigungsstatus": "Aktiv",
                "TrfGr": "E11",
                "MAK_Reporting": 0.75,
                "EUR_Reporting": 75.0,
                "Headcount": 1,
            },
            {
                "PersNr": "4",
                "Personalnummer": "4",
                "Is_Vacant": True,
                "Organisationseinheit": "OE B",
                "Jobfamily": "JF B",
                "Geschlecht": "m",
                "Alterskohorte": "60+",
                "Beschaeftigungsstatus": "Vakant",
                "Beschäftigungsstatus": "Vakant",
                "TrfGr": "E12",
                "MAK_Reporting": 1.0,
                "EUR_Reporting": 100.0,
                "Headcount": 1,
            },
        ]
    )


def test_orgunit_split_block_uses_same_pivot_for_table_and_export(monkeypatch):
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_contract_split")
    compact = _FakeCompact()
    capture_st, dataframes, downloads = _capture_render_helpers(monkeypatch, org_page)
    df = org_page._normalize_org_column(_analysis_snapshot())
    metric_config = org_page._get_metric_config(df, "MAK")

    org_page._render_org_split_block(
        df,
        "Geschlecht",
        "Geschlecht",
        "MAK",
        metric_config,
        compact,
        key_prefix="contract_org_gender",
        display_orgs=["OE A", "OE B"],
        value_label="IST",
    )

    assert len(capture_st.figures) == 1
    assert len(dataframes) == 1
    assert len(downloads) == 1
    exported = compact.exports[0]["df"]
    assert exported["Organisationseinheit"].tolist() == ["OE A", "OE B"]
    assert exported.set_index("Organisationseinheit").loc["OE A", "Gesamt"] == 1.5
    assert exported.set_index("Organisationseinheit").loc["OE B", "Gesamt"] == 0.75
    assert dataframes[0]["data"]["Gesamt"].tolist() == ["1.5", "0.8"]
    assert compact.exports[0]["kwargs"]["dimension_name"] == "Organisationseinheit x Geschlecht"
    assert compact.exports[0]["kwargs"]["lineage_ids"] == ["9-15"]
    assert downloads[0]["file_name"] == "contract_org_gender_geschlecht.xlsx"


def test_orgunit_split_comparison_contains_ist_simulation_delta_and_preserves_order():
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_contract_comparison")
    current = org_page._normalize_org_column(_analysis_snapshot())
    previous = current.copy()
    previous.loc[previous["Organisationseinheit"] == "OE A", "MAK_Reporting"] = [1.0, 1.0]
    metric_config = org_page._get_metric_config(current, "MAK")

    comparison = org_page._build_split_comparison(
        current,
        previous,
        "Geschlecht",
        "MAK",
        metric_config,
        ["OE A", "OE B"],
        value_label="Simulation",
        comparison_label="IST",
    )

    by_key = comparison.set_index(["Organisationseinheit", "Geschlecht"])
    assert comparison["Organisationseinheit"].tolist() == ["OE A", "OE A", "OE B"]
    assert by_key.loc[("OE A", "m"), "IST"] == 1.0
    assert by_key.loc[("OE A", "m"), "Simulation"] == 0.5
    assert by_key.loc[("OE A", "m"), "Delta"] == -0.5
    assert by_key.loc[("OE B", "w"), "Simulation"] == 0.75


def test_jobfamily_split_block_uses_same_pivot_for_table_and_export(monkeypatch):
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_contract_split")
    compact = _FakeCompact()
    capture_st, dataframes, downloads = _capture_render_helpers(monkeypatch, jobfamily_page)
    df = _analysis_snapshot()
    metric_config = jobfamily_page._get_metric_config(df, "MAK")

    jobfamily_page._render_jobfamily_split_block(
        df,
        "Geschlecht",
        "Geschlecht",
        "MAK",
        metric_config,
        compact,
        key_prefix="contract_jobfamily_gender",
        display_jobfamilies=["JF A", "JF B"],
    )

    assert len(capture_st.figures) == 1
    assert len(dataframes) == 1
    assert len(downloads) == 1
    exported = compact.exports[0]["df"]
    assert exported["Jobfamily"].tolist() == ["JF A", "JF B"]
    assert exported.set_index("Jobfamily").loc["JF A", "Gesamt"] == 1.5
    assert exported.set_index("Jobfamily").loc["JF B", "Gesamt"] == 0.75
    assert dataframes[0]["data"]["Gesamt"].tolist() == ["1.5", "0.8"]
    assert compact.exports[0]["kwargs"]["dimension_name"] == "Jobgruppe x Geschlecht"
    assert compact.exports[0]["kwargs"]["lineage_ids"] == ["8-15"]
    assert downloads[0]["file_name"] == "contract_jobfamily_gender_geschlecht.xlsx"


def test_role_summary_tables_exclude_vacancies_and_fallback_eur_to_mak():
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_contract_roles")
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_contract_roles")
    compact = _FakeCompact()
    df = org_page._normalize_org_column(_analysis_snapshot())

    org_metric, org_config, org_fallback = org_page._resolve_role_metric("EUR", df)
    job_metric, job_config, job_fallback = jobfamily_page._resolve_role_metric("EUR", df)

    assert (org_metric, org_fallback, org_config["value_col"]) == ("MAK", True, "MAK_Reporting")
    assert (job_metric, job_fallback, job_config["value_col"]) == ("MAK", True, "MAK_Reporting")

    org_summary = org_page._build_role_summary_table(df, ["OE A", "OE B"], compact)
    job_summary = jobfamily_page._build_role_summary_table(df, ["JF A", "JF B"], compact)

    assert org_summary.to_dict("records") == [
        {"Organisationseinheit": "OE A", "Köpfe": 2, "MAK": "1.5", "Ø MAK": "0.75"},
        {"Organisationseinheit": "OE B", "Köpfe": 1, "MAK": "0.8", "Ø MAK": "0.75"},
    ]
    assert job_summary.to_dict("records") == [
        {"Jobfamily": "JF A", "Köpfe": 2, "MAK": "1.5", "Ø MAK": "0.75"},
        {"Jobfamily": "JF B", "Köpfe": 1, "MAK": "0.8", "Ø MAK": "0.75"},
    ]


def test_orgunit_ranking_download_matches_visible_display_order_and_values(monkeypatch):
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_contract_ranking")
    compact = _FakeCompact()
    capture_st, dataframes, downloads = _capture_render_helpers(monkeypatch, org_page)
    df = org_page._normalize_org_column(_analysis_snapshot())
    metric_config = org_page._get_metric_config(df, "MAK")

    org_page._render_org_rangliste(
        df,
        "MAK",
        metric_config,
        compact,
        display_orgs=["OE B", "OE A"],
        value_label="Simulation",
    )

    assert len(capture_st.figures) == 1
    assert len(dataframes) == 1
    assert len(downloads) == 1

    visible = dataframes[0]["data"]
    exported = compact.exports[0]["df"]
    assert visible["Organisationseinheit"].tolist() == ["OE B", "OE A"]
    assert visible["Simulation"].tolist() == ["0.8", "1.5"]
    assert visible["Anteil"].tolist() == ["33.3%", "66.7%"]
    assert exported["Organisationseinheit"].tolist() == ["OE B", "OE A"]
    assert exported["Simulation"].tolist() == [0.75, 1.5]
    assert compact.exports[0]["kwargs"]["dimension_name"] == "Organisationseinheiten"
    assert compact.exports[0]["kwargs"]["table_title"] == "Rangliste Organisationseinheiten"
    assert compact.exports[0]["kwargs"]["lineage_ids"] == ["10-02", "10-07"]
    assert downloads[0]["file_name"] == "org_rangliste.xlsx"


def test_orgunit_comparison_download_matches_visible_columns_and_raw_values(monkeypatch):
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_contract_ranking_comparison")
    compact = _FakeCompact()
    capture_st, dataframes, downloads = _capture_render_helpers(monkeypatch, org_page)
    current = org_page._normalize_org_column(_analysis_snapshot())
    previous = current.copy()
    previous.loc[previous["PersNr"] == "2", "MAK_Reporting"] = 1.0
    metric_config = org_page._get_metric_config(current, "MAK")
    departures = pd.DataFrame(
        [
            {
                "Organisationseinheit": "OE A",
                "persnr": "2",
                "headcount_change": -1,
                "mak_change": -0.5,
            }
        ]
    )

    org_page._render_org_rangliste_comparison(
        current,
        previous,
        "MAK",
        metric_config,
        compact,
        display_orgs=["OE A", "OE B"],
        value_label="Simulation",
        comparison_label="IST",
        departure_events=departures,
    )

    assert len(capture_st.figures) == 1
    assert len(dataframes) == 1
    assert len(downloads) == 1

    expected_columns = [
        "Organisationseinheit",
        "IST",
        "Simulation",
        "Delta",
        "Delta %",
        "Abg\u00e4nge",
        "MAK-Verlust",
    ]
    visible = dataframes[0]["data"]
    exported = compact.exports[0]["df"]
    assert visible.columns.tolist() == expected_columns
    assert exported.columns.tolist() == expected_columns
    assert visible["Organisationseinheit"].tolist() == ["OE A", "OE B"]
    assert visible["IST"].tolist() == ["2.0", "0.8"]
    assert visible["Simulation"].tolist() == ["1.5", "0.8"]
    assert visible["Delta"].tolist() == ["-0.5", "0.0"]
    assert visible["Delta %"].tolist() == ["-25.0%", "0.0%"]

    exported_by_org = exported.set_index("Organisationseinheit")
    assert exported_by_org.loc["OE A", "IST"] == 2.0
    assert exported_by_org.loc["OE A", "Simulation"] == 1.5
    assert exported_by_org.loc["OE A", "Delta"] == -0.5
    assert exported_by_org.loc["OE A", "Abg\u00e4nge"] == 1
    assert exported_by_org.loc["OE A", "MAK-Verlust"] == 0.5
    assert compact.exports[0]["kwargs"]["table_title"] == "Rangliste Organisationseinheiten Vergleich"
    assert compact.exports[0]["kwargs"]["lineage_ids"] == ["10-03", "10-04", "10-07"]
    assert downloads[0]["file_name"] == "org_rangliste_vergleich.xlsx"


def test_jobfamily_ranking_download_matches_visible_display_order_and_values(monkeypatch):
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_contract_ranking")
    compact = _FakeCompact()
    capture_st, dataframes, downloads = _capture_render_helpers(monkeypatch, jobfamily_page)
    df = _analysis_snapshot()
    metric_config = jobfamily_page._get_metric_config(df, "MAK")

    jobfamily_page._render_jobfamily_rangliste(
        df,
        "MAK",
        metric_config,
        compact,
        value_label="Simulation",
        key_prefix="contract_jobfamily",
        top_n="Alle",
        display_jobfamilies=["JF B", "JF A"],
        is_simulation=True,
    )

    assert len(capture_st.figures) == 1
    assert len(dataframes) == 1
    assert len(downloads) == 1

    visible = dataframes[0]["data"]
    exported = compact.exports[0]["df"]
    assert visible["Jobfamily"].tolist() == ["JF B", "JF A"]
    assert visible["Simulation"].tolist() == ["0.8", "1.5"]
    assert visible["Anteil"].tolist() == ["33.3%", "66.7%"]
    assert exported["Jobfamily"].tolist() == ["JF B", "JF A"]
    assert exported["Simulation"].tolist() == [0.75, 1.5]
    assert compact.exports[0]["kwargs"]["dimension_name"] == "Jobgruppen"
    assert compact.exports[0]["kwargs"]["table_title"] == "Rangliste Jobgruppen"
    assert compact.exports[0]["kwargs"]["lineage_ids"] == ["11-02", "11-05"]
    assert downloads[0]["file_name"] == "contract_jobfamily_jobgruppen.xlsx"


def test_orgunit_ranking_with_real_compact_export_contains_lineage_report(monkeypatch):
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_contract_real_compact")
    clear_compact_page_module_cache()
    compact = load_compact_page_module()
    _capture_render_helpers(monkeypatch, org_page)
    df = org_page._normalize_org_column(_analysis_snapshot())
    metric_config = org_page._get_metric_config(df, "MAK")

    downloads = []
    monkeypatch.setattr(
        org_page,
        "lazy_excel_download_button_compat",
        lambda **kwargs: downloads.append({**kwargs, "data": kwargs["data_builder"]()}),
    )

    org_page._render_org_rangliste(
        df,
        "MAK",
        metric_config,
        compact,
        display_orgs=["OE A", "OE B"],
        value_label="Simulation",
    )

    workbook = pd.ExcelFile(io.BytesIO(downloads[0]["data"]))
    lineage = pd.read_excel(workbook, sheet_name="Lineage_Report")

    assert lineage["Lineage-ID"].tolist() == ["10-02", "10-07"]


def test_jobfamily_ranking_with_real_compact_export_contains_lineage_report(monkeypatch):
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_contract_real_compact")
    clear_compact_page_module_cache()
    compact = load_compact_page_module()
    _capture_render_helpers(monkeypatch, jobfamily_page)
    df = _analysis_snapshot()
    metric_config = jobfamily_page._get_metric_config(df, "MAK")

    downloads = []
    monkeypatch.setattr(
        jobfamily_page,
        "lazy_excel_download_button_compat",
        lambda **kwargs: downloads.append({**kwargs, "data": kwargs["data_builder"]()}),
    )

    jobfamily_page._render_jobfamily_rangliste(
        df,
        "MAK",
        metric_config,
        compact,
        value_label="Simulation",
        key_prefix="contract_jobfamily",
        top_n="Alle",
        display_jobfamilies=["JF A", "JF B"],
        is_simulation=True,
    )

    workbook = pd.ExcelFile(io.BytesIO(downloads[0]["data"]))
    lineage = pd.read_excel(workbook, sheet_name="Lineage_Report")

    assert lineage["Lineage-ID"].tolist() == ["11-02", "11-05"]
