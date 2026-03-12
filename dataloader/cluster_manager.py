
import pandas as pd
import os
import io
import streamlit as st
from typing import Dict, List, Optional, Tuple
from config.settings import BASE_DIR

# Path for persistent cluster mapping
# BASE_DIR imported from config.settings
# DEFAULT (Internal)
CLUSTER_FILE = os.path.join(BASE_DIR, "config", "cluster_mapping.xlsx")
# PRIMARY (User-modified external if available)
EXTERNAL_CLUSTER_FILE = os.path.abspath(os.path.join(BASE_DIR, "..", "Cluster-Daten", "OE_Cluster.xlsx"))

def get_active_cluster_file() -> str:
    """Returns the path to the most recent cluster mapping file."""
    # Priority: CLUSTER_FILE (Internal/UI-Upload) > EXTERNAL_CLUSTER_FILE (Original-Daten)
    # The UI upload saves to CLUSTER_FILE. To ensure it overrides the external file:
    if os.path.exists(CLUSTER_FILE):
        # We assume CLUSTER_FILE is either the factory default or a user upload.
        # If EXTERNAL_CLUSTER_FILE exists, it's "Original data".
        # If the user uses the UI to upload, they intend to override.
        # Check if internal file is newer than external (likely an upload)
        if os.path.exists(EXTERNAL_CLUSTER_FILE):
             if os.path.getmtime(CLUSTER_FILE) >= os.path.getmtime(EXTERNAL_CLUSTER_FILE):
                 return CLUSTER_FILE
             return EXTERNAL_CLUSTER_FILE
        return CLUSTER_FILE
    
    if os.path.exists(EXTERNAL_CLUSTER_FILE):
        return EXTERNAL_CLUSTER_FILE
    return CLUSTER_FILE

def generate_template_bytes(df_ma: pd.DataFrame, jf_definitions: Dict) -> bytes:
    """
    Generates an Excel template with current OrgUnits and JobFamilies (Position-based).
    Returns bytes for download.
    """
    # 1. OrgUnits
    oe_cols = ['Kürzel OrgEinheit', 'Organisationseinheit']
    if 'OrgEinheitNr' in df_ma.columns:
        oe_cols.insert(1, 'OrgEinheitNr')
    
    unique_oes = df_ma[oe_cols].drop_duplicates().sort_values('Kürzel OrgEinheit')
    unique_oes['Cluster'] = ""

    # 2. JobFamilies (Now Position-based)
    # Structure: ['Organisationseinheit', 'Planstelle', 'Jobfamily Cluster ID', 'Jobfamily Cluster']
    jf_cols = ['Organisationseinheit', 'Planstelle']
    if all(c in df_ma.columns for c in jf_cols):
        unique_pos = df_ma[jf_cols].drop_duplicates().sort_values(['Organisationseinheit', 'Planstelle'])
        unique_pos['Jobfamily Cluster ID'] = ""
        unique_pos['Jobfamily Cluster'] = ""
    else:
        # Fallback to legacy if columns missing or for empty templates
        unique_pos = pd.DataFrame(columns=['Organisationseinheit', 'Planstelle', 'Jobfamily Cluster ID', 'Jobfamily Cluster'])

    # Create Excel in memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        unique_oes.to_excel(writer, sheet_name='OrgUnits', index=False)
        unique_pos.to_excel(writer, sheet_name='JobFamilies', index=False)
        
    return output.getvalue()

def validate_and_save_clusters(uploaded_file) -> Tuple[bool, str]:
    """
    Validates the uploaded Excel and saves it to config.
    Returns (Success, Message).
    """
    try:
        # Load sheets
        xls = pd.ExcelFile(uploaded_file)
        if 'OrgUnits' not in xls.sheet_names or 'JobFamilies' not in xls.sheet_names:
            return False, "Die Datei muss die Tabellenblätter 'OrgUnits' und 'JobFamilies' enthalten."
        
        df_oe = pd.read_excel(xls, sheet_name='OrgUnits')
        df_jf = pd.read_excel(xls, sheet_name='JobFamilies')
        
        # Validate columns
        req_oe = ['Organisationseinheit', 'Cluster']
        for col in req_oe:
            if col not in df_oe.columns:
                return False, f"Tabelle 'OrgUnits' fehlt die Spalte '{col}'."
        
        # Check JobFamilies - support both new and old for migration
        has_new = 'Planstelle' in df_jf.columns and 'Jobfamily Cluster' in df_jf.columns
        has_old = 'Jobfamily' in df_jf.columns and 'Cluster' in df_jf.columns
        
        if not (has_new or has_old):
            return False, "Tabelle 'JobFamilies' muss entweder ('Planstelle', 'Jobfamily Cluster') oder ('Jobfamily', 'Cluster') enthalten."
        
        # Save to config
        with open(CLUSTER_FILE, "wb") as f:
            f.write(uploaded_file.getvalue())
            
        return True, "Cluster-Definitionen erfolgreich gespeichert."
        
    except Exception as e:
        return False, f"Fehler bei der Validierung: {str(e)}"

def load_cluster_mappings(uploaded_file: Optional[bytes] = None) -> Tuple[Dict[str, str], Dict]:
    """
    Loads saved cluster mappings.
    If uploaded_file is provided (bytes), it takes priority (session state).
    Returns (OE_Mapping, JF_Mapping)
    """
    oe_map = {}
    jf_map = {}
    
    try:
        if uploaded_file:
            xls = pd.ExcelFile(io.BytesIO(uploaded_file))
        else:
            active_file = get_active_cluster_file()
            if not os.path.exists(active_file):
                return oe_map, jf_map
            xls = pd.ExcelFile(active_file)
            
        df_oe = pd.read_excel(xls, sheet_name='OrgUnits')
        df_jf = pd.read_excel(xls, sheet_name='JobFamilies')
        
        # Fill empty clusters with "Unclustered"
        if not df_oe.empty and 'Cluster' in df_oe.columns:
            df_oe['Cluster'] = df_oe['Cluster'].fillna("Unclustered").astype(str).str.strip()
            oe_map = df_oe.set_index('Organisationseinheit')['Cluster'].to_dict()
        
        # Map JF based on structure
        # Note: Empty cluster cells are skipped (not added to mapping).
        # The downstream enrich_jf_clusters() will fall back to "Sonstiges".
        if not df_jf.empty:
            if 'Planstelle' in df_jf.columns and 'Jobfamily Cluster' in df_jf.columns:
                # New structure: (Organisationseinheit, Planstelle) -> Jobfamily Cluster
                if 'Organisationseinheit' in df_jf.columns:
                    df_jf['Organisationseinheit'] = df_jf['Organisationseinheit'].astype(str).str.strip()
                    df_jf['Planstelle'] = df_jf['Planstelle'].astype(str).str.strip()
                    df_jf['Jobfamily Cluster'] = df_jf['Jobfamily Cluster'].astype(str).str.strip()
                    for _, row in df_jf.iterrows():
                        val = row['Jobfamily Cluster']
                        if pd.isna(row.get('Jobfamily Cluster')) or val in ("", "nan", "Unclustered"):
                            continue  # Skip empty/unclustered → let fallback handle it
                        key = (row['Organisationseinheit'], row['Planstelle'])
                        jf_map[key] = val
                else:
                    df_jf['Planstelle'] = df_jf['Planstelle'].astype(str).str.strip()
                    df_jf['Jobfamily Cluster'] = df_jf['Jobfamily Cluster'].astype(str).str.strip()
                    valid = df_jf[~df_jf['Jobfamily Cluster'].isin(["", "nan", "Unclustered"])]
                    jf_map = valid.set_index('Planstelle')['Jobfamily Cluster'].to_dict()
            elif 'Jobfamily' in df_jf.columns and 'Cluster' in df_jf.columns:
                # Legacy structure
                df_jf['Cluster'] = df_jf['Cluster'].astype(str).str.strip()
                valid = df_jf[~df_jf['Cluster'].isin(["", "nan", "Unclustered"])]
                jf_map = valid.set_index('Jobfamily')['Cluster'].to_dict()
        
    except Exception as e:
        print(f"Error loading clusters: {e}")
        
    return oe_map, jf_map

def enrich_jf_clusters(
    df: pd.DataFrame,
    jf_map: Dict,
    snapshot_jf_map: Optional[Dict] = None
) -> pd.Series:
    """
    Core logic for JF-Cluster derivation with normalization and aliases.
    id: 72 - Normalization & Fallbacks

    Guarantee: Every row with a non-empty Jobfamily gets a deterministic
    JF-Cluster.  If no mapping exists the fallback is "Sonstiges" (NOT
    "Unclustered").  Only rows without ANY Jobfamily value remain
    "Sonstiges" (safe default).
    """
    if "Jobfamily" not in df.columns:
        return pd.Series("Sonstiges", index=df.index)

    # 1. Aliases & Fallbacks
    # id: 72 - Normalization & Permanent Fallbacks
    # NOTE: DO NOT REMOVE the "sonstige" -> "Sonstiges" rule.
    # It ensures that Azubis in training (labeled as "Sonstige") are correctly clustered
    # even without an explicit mapping. "Trainee" follows the same logic.
    alias_map = {
        "sonstige": "Sonstiges",
        "sonstiges": "Sonstiges",
        "trainee": "Sonstiges",
        "azubi": "Sonstiges"
    }

    # 2. Key Normalization
    # Normalize the lookup maps to lowercase for case-insensitive matching
    norm_jf_map = {str(k).strip().lower(): v for k, v in jf_map.items() if not isinstance(k, tuple)}
    norm_snap_map = {str(k).strip().lower(): v for k, v in snapshot_jf_map.items()} if snapshot_jf_map else {}

    # 3. Apply Mapping Sequence
    s_jf_raw = df["Jobfamily"].astype(str).str.strip()
    s_jf_lower = s_jf_raw.str.lower()

    # Priority (höchste zuletzt im merge, damit sie überschreiben):
    # A) Direct Map (External/Internal)  — höchste Priorität
    # B) Snapshot Map (Inherited)
    # C) Alias Map (Fallback)
    # D) "Sonstiges" (universal safety-net – NO "Unclustered" leak)
    # Einzelner map()-Aufruf vermeidet fillna-Kette und pandas FutureWarning
    # (Downcasting object dtype arrays on .fillna is deprecated).
    combined_map = {**alias_map, **norm_snap_map, **norm_jf_map}
    res = s_jf_lower.map(combined_map)

    # Final fallback: "Sonstiges" for every row that still has no cluster.
    # This guarantees JF-unclustered = 0 in all downstream charts.
    return res.fillna("Sonstiges")

def apply_clusters_to_snapshot(df: pd.DataFrame, uploaded_file: Optional[bytes] = None) -> pd.DataFrame:
    """
    Adds Cluster columns to snapshot based on mapping file.
    """
    oe_map, jf_map = load_cluster_mappings(uploaded_file)
    
    # Map OE Clusters
    if "Organisationseinheit" in df.columns:
        df["OE-Cluster"] = df["Organisationseinheit"].map(oe_map).fillna("Unclustered")
    
    # Map JF Clusters
    if not jf_map:
        df["JF-Cluster"] = enrich_jf_clusters(df, {})
    else:
        first_key = next(iter(jf_map.keys()))
        if isinstance(first_key, tuple):
             # Tuple-based key (Org, Pos) -> Use specific logic for high-fidelity records
             if "Organisationseinheit" in df.columns and "Planstelle" in df.columns:
                 s_org = df["Organisationseinheit"].astype(str).str.strip()
                 s_pos = df["Planstelle"].astype(str).str.strip()
                 keys = list(zip(s_org, s_pos))
                 # Fallback to JF matching if tuple lookup fails
                 tuple_res = pd.Series([jf_map.get(k) for k in keys], index=df.index)
                 df["JF-Cluster"] = tuple_res.fillna(enrich_jf_clusters(df, jf_map))
             else:
                 df["JF-Cluster"] = enrich_jf_clusters(df, jf_map)
        else:
             # Standard string-based mapping
             df["JF-Cluster"] = enrich_jf_clusters(df, jf_map)

    # Safety-net: Replace any lingering "Unclustered" in JF-Cluster with "Sonstiges"
    # This catches edge cases from tuple lookups or stale mapping values.
    if "JF-Cluster" in df.columns:
        mask_uncl = (df["JF-Cluster"] == "Unclustered") | df["JF-Cluster"].isna()
        if mask_uncl.any():
            df.loc[mask_uncl, "JF-Cluster"] = "Sonstiges"

    return df

def is_clustering_active(uploaded_file: Optional[bytes] = None) -> bool:
    """
    Returns True if a valid cluster mapping file exists (or is provided) and has entries.
    """
    try:
        if uploaded_file:
            xls = pd.ExcelFile(io.BytesIO(uploaded_file))
        else:
            active_file = get_active_cluster_file()
            if not os.path.exists(active_file):
                return False
            xls = pd.ExcelFile(active_file)
            
        df_oe = pd.read_excel(xls, sheet_name='OrgUnits')
        if not df_oe.empty and 'Cluster' in df_oe.columns:
            if df_oe['Cluster'].dropna().astype(str).str.strip().ne("").any():
                return True
        
        df_jf = pd.read_excel(xls, sheet_name='JobFamilies')
        for col in ['Jobfamily Cluster', 'Cluster']:
            if col in df_jf.columns:
                if df_jf[col].dropna().astype(str).str.strip().ne("").any():
                    return True
            
        return False
    except:
        return False
