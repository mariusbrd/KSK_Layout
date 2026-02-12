
import pandas as pd
import os
import io
import streamlit as st
from typing import Dict, List, Optional, Tuple

# Path for persistent cluster mapping
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# DEFAULT (Internal)
CLUSTER_FILE = os.path.join(BASE_DIR, "config", "cluster_mapping.xlsx")
# PRIMARY (User-modified external if available)
EXTERNAL_CLUSTER_FILE = os.path.abspath(os.path.join(BASE_DIR, "..", "Cluster-Daten", "OE_Cluster.xlsx"))

def get_active_cluster_file() -> str:
    """Returns the path to the most recent cluster mapping file."""
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

def load_cluster_mappings() -> Tuple[Dict[str, str], Dict]:
    """
    Loads saved cluster mappings.
    Returns (OE_Mapping, JF_Mapping)
    """
    oe_map = {}
    jf_map = {}
    
    active_file = get_active_cluster_file()
    if not os.path.exists(active_file):
        return oe_map, jf_map
        
    try:
        xls = pd.ExcelFile(active_file)
        df_oe = pd.read_excel(xls, sheet_name='OrgUnits')
        df_jf = pd.read_excel(xls, sheet_name='JobFamilies')
        
        # Fill empty clusters with "Unclustered"
        if not df_oe.empty and 'Cluster' in df_oe.columns:
            df_oe['Cluster'] = df_oe['Cluster'].fillna("Unclustered").astype(str).str.strip()
            oe_map = df_oe.set_index('Organisationseinheit')['Cluster'].to_dict()
        
        # Map JF based on structure
        if not df_jf.empty:
            if 'Planstelle' in df_jf.columns and 'Jobfamily Cluster' in df_jf.columns:
                # New structure: (Organisationseinheit, Planstelle) -> Jobfamily Cluster
                if 'Organisationseinheit' in df_jf.columns:
                    df_jf['Organisationseinheit'] = df_jf['Organisationseinheit'].astype(str).str.strip()
                    df_jf['Planstelle'] = df_jf['Planstelle'].astype(str).str.strip()
                    df_jf['Jobfamily Cluster'] = df_jf['Jobfamily Cluster'].fillna("Unclustered").astype(str).str.strip()
                    for _, row in df_jf.iterrows():
                        key = (row['Organisationseinheit'], row['Planstelle'])
                        jf_map[key] = row['Jobfamily Cluster']
                else:
                    df_jf['Planstelle'] = df_jf['Planstelle'].astype(str).str.strip()
                    df_jf['Jobfamily Cluster'] = df_jf['Jobfamily Cluster'].fillna("Unclustered").astype(str).str.strip()
                    jf_map = df_jf.set_index('Planstelle')['Jobfamily Cluster'].to_dict()
            elif 'Jobfamily' in df_jf.columns and 'Cluster' in df_jf.columns:
                # Legacy structure
                df_jf['Cluster'] = df_jf['Cluster'].fillna("Unclustered").astype(str).str.strip()
                jf_map = df_jf.set_index('Jobfamily')['Cluster'].to_dict()
        
    except Exception as e:
        print(f"Error loading clusters: {e}")
        
    return oe_map, jf_map

def apply_clusters_to_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds Cluster columns to snapshot based on mapping file.
    """
    oe_map, jf_map = load_cluster_mappings()
    
    # Map OE Clusters
    if "Organisationseinheit" in df.columns:
        df["OE-Cluster"] = df["Organisationseinheit"].map(oe_map).fillna("Unclustered")
    
    # Map JF Clusters
    if not jf_map:
        df["JF-Cluster"] = "Unclustered"
    else:
        first_key = next(iter(jf_map.keys()))
        if isinstance(first_key, tuple):
             # Tuple-based key (Org, Pos)
             if "Organisationseinheit" in df.columns and "Planstelle" in df.columns:
                 s_org = df["Organisationseinheit"].astype(str).str.strip()
                 s_pos = df["Planstelle"].astype(str).str.strip()
                 keys = list(zip(s_org, s_pos))
                 df["JF-Cluster"] = [jf_map.get(k, "Unclustered") for k in keys]
             else:
                 df["JF-Cluster"] = "Unclustered"
        elif "Planstelle" in df.columns and any(k in df["Planstelle"].values for k in jf_map.keys()):
             # Planstelle-based (string)
             df["JF-Cluster"] = df["Planstelle"].astype(str).str.strip().map(jf_map).fillna("Unclustered")
        elif "Jobfamily" in df.columns:
             # Jobfamily-based (string)
             df["JF-Cluster"] = df["Jobfamily"].map(jf_map).fillna("Unclustered")
        else:
             df["JF-Cluster"] = "Unclustered"
        
    return df

def is_clustering_active() -> bool:
    """
    Returns True if a valid cluster mapping file exists and has entries.
    """
    active_file = get_active_cluster_file()
    if not os.path.exists(active_file):
        return False
    try:
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
