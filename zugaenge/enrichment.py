"""
Shared enrichment pipeline for Zugaenge events.

Used by both Page 4 (Prognose: Zugaenge) and Page 5 (Prognose: Hybrid)
to ensure identical JF-Cluster mapping, fallback handling, and debug output.
"""

import pandas as pd
from typing import Dict, Any, Optional, Set


# ---------------------------------------------------------------------------
# 1. JF-to-Cluster Map Builder
# ---------------------------------------------------------------------------

def build_jf_to_cluster_map(snapshot_df: pd.DataFrame) -> dict:
    """
    Build {Jobfamily: JF-Cluster} mapping from snapshot.
    Excludes 'Unclustered', empty, and nan values.
    """
    if "Jobfamily" not in snapshot_df.columns or "JF-Cluster" not in snapshot_df.columns:
        return {}
    return {
        str(k).strip(): v
        for k, v in zip(snapshot_df["Jobfamily"], snapshot_df["JF-Cluster"])
        if v != "Unclustered" and str(v).strip() not in ("", "nan")
    }


# ---------------------------------------------------------------------------
# 2. Known JF Keys Builder (for fallback detection)
# ---------------------------------------------------------------------------

def build_known_jf_keys(run_params: dict, snapshot_df: pd.DataFrame) -> set:
    """
    Build the complete set of known Jobfamily keys (lowercased) from all sources.
    Used to distinguish real mapping gaps from deliberate 'Sonstiges' assignments.

    Sources:
    - Hardcoded aliases (sonstige, trainee, azubi)
    - jf_to_cluster_map from run_params
    - Cluster file (via load_cluster_mappings)
    - Snapshot inheritance (non-Sonstiges JF-Cluster entries)
    """
    _alias_keys = {"sonstige", "sonstiges", "trainee", "azubi"}
    _known = set(_alias_keys)

    # From run_params
    _jf_param_map = run_params.get("azubi", {}).get("jf_to_cluster_map", {})
    _known.update(str(k).strip().lower() for k in _jf_param_map.keys())

    # From cluster file
    try:
        from dataloader.cluster_manager import load_cluster_mappings as _lcm
        _, _file_jf_map = _lcm()
        for k in _file_jf_map.keys():
            if not isinstance(k, tuple):
                _known.add(str(k).strip().lower())
    except Exception:
        pass

    # From snapshot inheritance (only non-Sonstiges clusters)
    if "Jobfamily" in snapshot_df.columns and "JF-Cluster" in snapshot_df.columns:
        _snap_clean = snapshot_df.dropna(subset=["Jobfamily", "JF-Cluster"])
        _snap_clean = _snap_clean[_snap_clean["JF-Cluster"] != "Unclustered"]
        _snap_clean = _snap_clean[_snap_clean["JF-Cluster"] != "Sonstiges"]
        _known.update(str(k).strip().lower() for k in _snap_clean["Jobfamily"])

    return _known


# ---------------------------------------------------------------------------
# 3. Full Enrichment Pipeline
# ---------------------------------------------------------------------------

def enrich_zugaenge_events(
    events_df: pd.DataFrame,
    snapshot_df: pd.DataFrame,
    run_params: dict,
) -> pd.DataFrame:
    """
    Full multi-stage enrichment pipeline for Zugaenge events.
    Adds/corrects OE-Cluster, JF-Cluster, _jf_fallback, Diagnose-Quelle.

    Guarantees: No 'Unclustered' in JF-Cluster after enrichment.
    """
    if events_df.empty:
        return events_df

    events_df = events_df.copy()

    # Normalize column names
    if "org_unit" in events_df.columns and "Organisationseinheit" not in events_df.columns:
        events_df = events_df.rename(columns={"org_unit": "Organisationseinheit"})

    # --- Pre-prep normalization ---
    events_df["persnr_str"] = (
        events_df["persnr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    )
    snapshot_df = snapshot_df.copy()
    snapshot_df["PersNr_str"] = (
        snapshot_df["PersNr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    )
    snapshot_unique = snapshot_df.drop_duplicates(subset=["PersNr_str"])

    # --- 1. OE-Cluster Enrichment ---
    if "OE-Cluster" in snapshot_df.columns:
        oe_cluster_map = snapshot_unique.set_index("PersNr_str")["OE-Cluster"].to_dict()
        mapped_oe = events_df["persnr_str"].map(oe_cluster_map)

        if "OE-Cluster" in events_df.columns:
            events_df["OE-Cluster"] = events_df["OE-Cluster"].fillna(mapped_oe).fillna("Sonstiges")
        else:
            events_df["OE-Cluster"] = mapped_oe.fillna("Sonstiges")

        # Secondary fallback via Organisationseinheit
        org_cluster_map = snapshot_df.set_index("Organisationseinheit")["OE-Cluster"].to_dict()
        mask_unclustered = (events_df["OE-Cluster"] == "Unclustered") | (events_df["OE-Cluster"].isna())
        if mask_unclustered.any() and "Organisationseinheit" in events_df.columns:
            fill_vals = events_df.loc[mask_unclustered, "Organisationseinheit"].map(org_cluster_map)
            events_df.loc[mask_unclustered, "OE-Cluster"] = fill_vals.fillna("Sonstiges")
    else:
        if "OE-Cluster" not in events_df.columns:
            events_df["OE-Cluster"] = "Sonstiges"

    # --- 2. JF-Cluster Enrichment (Multi-Stage) ---
    if "JF-Cluster" in snapshot_df.columns:
        # Stage A: PersNr lookup
        jf_cluster_map = snapshot_unique.set_index("PersNr_str")["JF-Cluster"].to_dict()
        jf_cluster_map = {k: v for k, v in jf_cluster_map.items() if v != "Unclustered"}
        mapped_jf = events_df["persnr_str"].map(jf_cluster_map)

        if "JF-Cluster" in events_df.columns:
            existing = events_df["JF-Cluster"]
            existing = existing.where(existing.notna() & (existing != "Unclustered"))
            events_df["JF-Cluster"] = existing.fillna(mapped_jf)
        else:
            events_df["JF-Cluster"] = mapped_jf

        # Stage B: Planstelle/OrgUnit tuple lookup (External Cluster File)
        mask_unclustered_jf = (events_df["JF-Cluster"] == "Unclustered") | (events_df["JF-Cluster"].isna())
        if mask_unclustered_jf.any():
            try:
                from dataloader.cluster_manager import load_cluster_mappings
                _, jf_map = load_cluster_mappings()

                if jf_map:
                    first_key = next(iter(jf_map.keys()), None)
                    if isinstance(first_key, tuple):
                        if "Organisationseinheit" in events_df.columns and "Planstelle" in events_df.columns:
                            s_org = events_df.loc[mask_unclustered_jf, "Organisationseinheit"].astype(str).str.strip()
                            s_pos = events_df.loc[mask_unclustered_jf, "Planstelle"].astype(str).str.strip()
                            keys = list(zip(s_org, s_pos))
                            events_df.loc[mask_unclustered_jf, "JF-Cluster"] = [jf_map.get(k, None) for k in keys]
                    elif "Planstelle" in events_df.columns:
                        s_pos_clean = events_df.loc[mask_unclustered_jf, "Planstelle"].astype(str).str.strip()
                        jf_map_clean = {str(k).strip(): v for k, v in jf_map.items()}
                        events_df.loc[mask_unclustered_jf, "JF-Cluster"] = s_pos_clean.map(jf_map_clean)
            except Exception:
                pass

        # Stage C: Jobfamily alias mapping (centralized function)
        mask_still_uncl = (events_df["JF-Cluster"] == "Unclustered") | (events_df["JF-Cluster"].isna())
        if mask_still_uncl.any() and "Jobfamily" in events_df.columns:
            from dataloader.cluster_manager import enrich_jf_clusters

            snap_clean = snapshot_df.dropna(subset=["Jobfamily", "JF-Cluster"])
            snap_clean = snap_clean[snap_clean["JF-Cluster"] != "Unclustered"]
            snap_jf_map = {str(k).strip(): v for k, v in zip(snap_clean["Jobfamily"], snap_clean["JF-Cluster"])}

            subset = events_df.loc[mask_still_uncl]
            events_df.loc[mask_still_uncl, "JF-Cluster"] = enrich_jf_clusters(subset, {}, snap_jf_map)

        # Stage D: Final safety-net
        mask_final = (events_df["JF-Cluster"] == "Unclustered") | (events_df["JF-Cluster"].isna())
        if mask_final.any():
            events_df.loc[mask_final, "JF-Cluster"] = "Sonstiges"

    else:
        if "JF-Cluster" not in events_df.columns:
            events_df["JF-Cluster"] = "Sonstiges"

    # --- 3. Diagnose-Quelle ---
    def _get_diag_source(row):
        t = str(row.get("type", ""))
        if t == "Azubi_Hire":
            return "Azubi neu in Ausbildung"
        if t == "Azubi_Conversion_In":
            return "Azubi Übernahme"
        if t == "New_Hire":
            return "Neueinstellung"
        if t == "Trainee_Hire":
            return "Trainee-Einstellung"
        if "Azubi" in str(row.get("source", "")):
            return "Bestands-Azubi"
        return "Sonstige Zugänge"

    events_df["Diagnose-Quelle"] = events_df.apply(_get_diag_source, axis=1)

    # --- 4. Fallback Flag ---
    _known_jf_keys = build_known_jf_keys(run_params, snapshot_df)

    if "Jobfamily" in events_df.columns:
        _jf_lower = events_df["Jobfamily"].astype(str).str.strip().str.lower()
        _is_sonstiges = events_df["JF-Cluster"] == "Sonstiges"
        _not_in_mapping = ~_jf_lower.isin(_known_jf_keys)
        _is_orgunit_deliberate = (
            events_df.get("_jf_orgunit_mode", pd.Series(False, index=events_df.index))
            .fillna(False)
            .astype(bool)
        )
        events_df["_jf_fallback"] = _is_sonstiges & _not_in_mapping & ~_is_orgunit_deliberate
    else:
        events_df["_jf_fallback"] = False

    # --- Cleanup ---
    events_df.drop(columns=["persnr_str"], inplace=True, errors="ignore")
    snapshot_df.drop(columns=["PersNr_str"], inplace=True, errors="ignore")

    return events_df


# ---------------------------------------------------------------------------
# 4. Debug/Audit Renderer (requires Streamlit)
# ---------------------------------------------------------------------------

def render_zugaenge_debug_sections(
    events_df: pd.DataFrame,
    run_params: dict,
    snapshot_df: pd.DataFrame,
):
    """
    Render the 5-section debug output for Zugaenge enrichment diagnostics.
    Sections: Unclustered A/B, A) Fallback Summary, B) by Event Type,
    C) Top 20 JF, D) Example Rows, E) Root Cause Diagnosis.

    Requires Streamlit (import inside function to keep module importable without st).
    """
    import streamlit as st

    # --- Chart basis: same event types used in JF charts ---
    _chart_types = ["Azubi_Hire", "Azubi_Conversion_In", "New_Hire", "Trainee_Hire"]
    _chart_basis = events_df[events_df["type"].isin(_chart_types)].copy()
    _total_chart = len(_chart_basis)
    _total_mak = _chart_basis["mak"].sum() if "mak" in _chart_basis.columns else 0.0

    # --- Unclustered-Check (must be 0) ---
    hire_events = events_df[events_df["type"] == "Azubi_Hire"]
    tk_events = events_df[events_df["type"] == "Azubi_Conversion_In"]

    col_chk1, col_chk2 = st.columns(2)
    with col_chk1:
        uncl_a = (hire_events["JF-Cluster"] == "Unclustered").sum() if not hire_events.empty else 0
        if uncl_a == 0:
            st.success(f"JF-unclustered A = 0  ({len(hire_events)} Azubi-Hire Events)")
        else:
            st.error(f"JF-unclustered A = {uncl_a} (should be 0!)")
    with col_chk2:
        uncl_b = (tk_events["JF-Cluster"] == "Unclustered").sum() if not tk_events.empty else 0
        if uncl_b == 0:
            st.success(f"JF-unclustered B = 0  ({len(tk_events)} Conversion-In Events)")
        else:
            st.error(f"JF-unclustered B = {uncl_b} (should be 0!)")

    uncl_total = (_chart_basis["JF-Cluster"] == "Unclustered").sum() if not _chart_basis.empty else 0
    if uncl_total == 0:
        st.success(f"Unclustered Breakdown (JF-Charts Basis) = leer  (Basis: {_total_chart} Einträge)")
    else:
        st.error(f"{uncl_total} 'Unclustered' in JF-Charts Basis!")

    # --- A) Fallback Summary ---
    st.divider()
    st.markdown("#### A) Fallback-Summary")

    _has_flag = "_jf_fallback" in _chart_basis.columns
    if _has_flag:
        _fb_mask = _chart_basis["_jf_fallback"] == True
    else:
        _alias_jfs = {"sonstige", "sonstiges", "trainee", "azubi"}
        _jf_low = (
            _chart_basis["Jobfamily"].astype(str).str.strip().str.lower()
            if "Jobfamily" in _chart_basis.columns
            else pd.Series("", index=_chart_basis.index)
        )
        _fb_mask = (_chart_basis["JF-Cluster"] == "Sonstiges") & (~_jf_low.isin(_alias_jfs))

    _orgunit_mask = (
        _chart_basis.get("_jf_orgunit_mode", pd.Series(False, index=_chart_basis.index))
        .fillna(False)
        .astype(bool)
    )
    _orgunit_count = _orgunit_mask.sum()
    _orgunit_mak = _chart_basis.loc[_orgunit_mask, "mak"].sum() if "mak" in _chart_basis.columns else 0.0

    _fb_count = _fb_mask.sum()
    _fb_mak = _chart_basis.loc[_fb_mask, "mak"].sum() if "mak" in _chart_basis.columns else 0.0
    _sonstiges_total = (_chart_basis["JF-Cluster"] == "Sonstiges").sum() if not _chart_basis.empty else 0
    _sonstiges_alias = _sonstiges_total - _fb_count - _orgunit_count

    col_s1, col_s2, col_s3, col_s4 = st.columns(4)
    col_s1.metric("Fallback (Mapping fehlt)", f"{_fb_count} HC / {_fb_mak:.1f} MAK")
    col_s2.metric("Sonstiges (OrgUnit-Modus)", f"{_orgunit_count} HC / {_orgunit_mak:.1f} MAK")
    col_s3.metric("Sonstiges (Alias: Azubi/Trainee)", f"{_sonstiges_alias}")
    col_s4.metric(
        "Anteil Fallback an Chart-Basis",
        f"{(_fb_count / _total_chart * 100):.1f} %" if _total_chart > 0 else "n/a",
    )

    st.caption(
        f"Von {_sonstiges_total} Einträgen mit JF-Cluster='Sonstiges': "
        f"{_sonstiges_alias} via Alias (Azubi/Trainee), "
        f"{_orgunit_count} via OrgUnit-Modus (fachlich korrekt, kein JF-Mapping erwartet), "
        f"{_fb_count} echte Fallbacks (Mapping fehlt)."
    )

    # --- B) Fallback by Event Type ---
    st.divider()
    st.markdown("#### B) Fallback nach Eventtyp")

    _fb_rows = _chart_basis[_fb_mask]
    if not _fb_rows.empty:
        _fb_by_type = (
            _fb_rows.groupby("type")
            .agg(
                Count=("type", "size"),
                MAK=("mak", "sum") if "mak" in _fb_rows.columns else ("type", "size"),
            )
            .sort_values("Count", ascending=False)
            .reset_index()
        )
        _fb_by_type.columns = ["Eventtyp", "Count (HC)", "MAK"]
        _fb_by_type["MAK"] = _fb_by_type["MAK"].round(1)
        st.dataframe(_fb_by_type, use_container_width=True, hide_index=True)
    else:
        st.info("Keine Fallback-Einträge vorhanden.")

    # --- C) Top 20 Jobfamilies ---
    st.divider()
    st.markdown("#### C) Top 20 Jobfamilies -> Sonstiges (Fallback)")

    if not _fb_rows.empty and "Jobfamily" in _fb_rows.columns:
        _fb_jf_agg = (
            _fb_rows.groupby("Jobfamily")
            .agg(
                Count=("Jobfamily", "size"),
                MAK=("mak", "sum") if "mak" in _fb_rows.columns else ("Jobfamily", "size"),
            )
            .sort_values("Count", ascending=False)
            .head(20)
            .reset_index()
        )
        _fb_jf_types = (
            _fb_rows.groupby("Jobfamily")["type"]
            .apply(lambda x: ", ".join(x.value_counts().head(3).index.tolist()))
            .reset_index(name="Top Eventtypen")
        )
        _fb_jf_agg = _fb_jf_agg.merge(_fb_jf_types, on="Jobfamily", how="left")
        _fb_jf_agg.columns = ["Jobfamily", "Count (HC)", "MAK", "Top Eventtypen"]
        _fb_jf_agg["MAK"] = _fb_jf_agg["MAK"].round(1)
        st.dataframe(_fb_jf_agg, use_container_width=True, hide_index=True)
    else:
        st.info("Keine Fallback-Jobfamilies vorhanden.")

    # --- D) Example Rows ---
    st.divider()
    st.markdown("#### D) Beispielzeilen (Fallback)")

    if not _fb_rows.empty:
        _example_cols = [
            "date", "type", "Jobfamily", "Organisationseinheit",
            "Planstelle", "JF-Cluster", "OE-Cluster", "mak",
        ]
        _example_cols = [c for c in _example_cols if c in _fb_rows.columns]

        _top_types = _fb_rows["type"].value_counts().head(3).index.tolist()
        for _tt in _top_types:
            _tt_rows = _fb_rows[_fb_rows["type"] == _tt]
            st.markdown(f"**{_tt}** ({len(_tt_rows)} Fallback-Einträge) — 5 Beispiele:")
            st.dataframe(
                _tt_rows[_example_cols].head(5).sort_values("date"),
                use_container_width=True,
                hide_index=True,
            )
    else:
        st.info("Keine Beispielzeilen (kein Fallback).")

    # --- E) Root Cause Diagnosis ---
    st.divider()
    st.markdown("#### E) Ursachen-Diagnose")

    if not _fb_rows.empty and "Jobfamily" in _fb_rows.columns:
        _fb_jf_unique = sorted(_fb_rows["Jobfamily"].dropna().unique().tolist())
        st.markdown("**Jobfamilies im Fallback vs. aktive Mapping-Quellen:**")

        try:
            from dataloader.cluster_manager import load_cluster_mappings as _lcm_diag

            _, _diag_jf_map = _lcm_diag()
            _diag_keys_lower = {
                str(k).strip().lower() for k in _diag_jf_map.keys() if not isinstance(k, tuple)
            }

            _diag_param_map = run_params.get("azubi", {}).get("jf_to_cluster_map", {})
            _diag_param_keys = {str(k).strip().lower() for k in _diag_param_map.keys()}

            _diag_rows = []
            for jf in _fb_jf_unique:
                jf_l = str(jf).strip().lower()
                in_file = jf_l in _diag_keys_lower
                in_param = jf_l in _diag_param_keys
                near = [
                    k
                    for k in _diag_keys_lower
                    if k.replace(" ", "") == jf_l.replace(" ", "") and k != jf_l
                ]
                _diag_rows.append(
                    {
                        "Jobfamily": jf,
                        "In Cluster-Datei": "Ja" if in_file else "NEIN",
                        "In Snapshot-Map": "Ja" if in_param else "NEIN",
                        "Near-Match (Format?)": ", ".join(near) if near else "-",
                    }
                )

            _diag_df = pd.DataFrame(_diag_rows)
            st.dataframe(_diag_df, use_container_width=True, hide_index=True)

            _missing_count = sum(
                1
                for r in _diag_rows
                if r["In Cluster-Datei"] == "NEIN" and r["In Snapshot-Map"] == "NEIN"
            )
            if _missing_count > 0:
                st.warning(
                    f"{_missing_count} Jobfamily(s) fehlen in allen Mapping-Quellen. "
                    "Diese sollten in der Cluster-Datei ergänzt werden."
                )
            else:
                st.info(
                    "Alle Fallback-Jobfamilies sind in mindestens einer Quelle vorhanden "
                    "(evtl. Format-Abweichung)."
                )

            if _diag_jf_map:
                _first_k = next(iter(_diag_jf_map.keys()))
                _map_type = (
                    "Tupel-basiert (Org, Planstelle)"
                    if isinstance(_first_k, tuple)
                    else "String-basiert (Jobfamily)"
                )
                st.caption(
                    f"Cluster-Datei Mapping-Typ: **{_map_type}** | Einträge: **{len(_diag_jf_map)}**"
                )
                if isinstance(_first_k, tuple):
                    st.caption(
                        "Bei Tupel-basiertem Mapping wird nach (Organisationseinheit, Planstelle) "
                        "gesucht, nicht nach Jobfamily-Namen. Deshalb können Jobfamilies, die als "
                        "String vorhanden sind, trotzdem nicht gemappt werden."
                    )
            else:
                st.caption("Cluster-Datei: Kein JF-Mapping geladen (leer oder Datei fehlt).")
        except Exception as _diag_ex:
            st.warning(f"Diagnose-Fehler: {_diag_ex}")
    else:
        st.success("Keine Fallback-Einträge — alle Jobfamilies sind korrekt gemappt.")

    st.divider()
    st.caption(
        "Hinweis: 'Fallback' = Jobfamily gesetzt, aber kein Mapping vorhanden → JF-Cluster wird "
        "'Sonstiges'. 'Explizit gemappt' = Sonstige/Trainee/Azubi via Alias-Regel → fachlich korrekt."
    )
