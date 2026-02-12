
import pandas as pd
import os
import sys
import io

# Setup Path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dataloader.loader import load_and_prepare_data
from dataloader.jobfamily_matcher import load_jobfamily_definitions
from dataloader.cluster_manager import generate_template_bytes, validate_and_save_clusters, load_cluster_mappings, CLUSTER_FILE

def test_clustering_workflow():
    print("--- Starting Clustering Workflow Test ---")
    
    # Backup existing cluster file if any
    backup_file = CLUSTER_FILE + ".bak"
    has_backup = False
    if os.path.exists(CLUSTER_FILE):
        os.rename(CLUSTER_FILE, backup_file)
        has_backup = True
        print(f"Backed up existing cluster file to {backup_file}")

    try:
        # 1. Load data
        print("Loading data...")
        df_ma, _, _, _ = load_and_prepare_data()
        jf_defs = load_jobfamily_definitions()
        
        # 2. Generate Template
        print("Generating template...")
        template_bytes = generate_template_bytes(df_ma, jf_defs)
        
        # Verify template structure
        xls = pd.ExcelFile(io.BytesIO(template_bytes))
        assert 'OrgUnits' in xls.sheet_names, "Missing OrgUnits sheet"
        assert 'JobFamilies' in xls.sheet_names, "Missing JobFamilies sheet"
        
        df_oe_template = pd.read_excel(xls, sheet_name='OrgUnits')
        df_jf_template = pd.read_excel(xls, sheet_name='JobFamilies')
        
        print(f"Template generated with {len(df_oe_template)} OEs and {len(df_jf_template)} JFs.")
        
        # 3. Modify Template (Simulate user input)
        print("Modifying template...")
        df_oe_template['Cluster'] = "Test-OE-Cluster"
        df_jf_template['Cluster'] = "Test-JF-Cluster"
        
        modified_output = io.BytesIO()
        with pd.ExcelWriter(modified_output, engine='xlsxwriter') as writer:
            df_oe_template.to_excel(writer, sheet_name='OrgUnits', index=False)
            df_jf_template.to_excel(writer, sheet_name='JobFamilies', index=False)
        
        # 4. Validate and Save
        print("Validating and saving...")
        valid_file = io.BytesIO(modified_output.getvalue())
        success, msg = validate_and_save_clusters(valid_file)
        print(f"Result: {success}, {msg}")
        assert success, f"Validation failed: {msg}"
        assert os.path.exists(CLUSTER_FILE), "Cluster file not saved"
        
        # 5. Reload and Verify Enrichment
        print("Reloading data with clusters...")
        # Clear cache to force reload
        # Since we are in a script, we just call the function again.
        # But wait, enrichment happens inside load_and_prepare_data.
        df_enriched, _, _, _ = load_and_prepare_data()
        
        assert 'OE-Cluster' in df_enriched.columns, "OE-Cluster column missing"
        assert 'JF-Cluster' in df_enriched.columns, "JF-Cluster column missing"
        
        unique_oe_clusters = df_enriched['OE-Cluster'].unique()
        unique_jf_clusters = df_enriched['JF-Cluster'].unique()
        
        print(f"Found OE Clusters: {unique_oe_clusters}")
        print(f"Found JF Clusters: {unique_jf_clusters}")
        
        assert "Test-OE-Cluster" in unique_oe_clusters or len(df_enriched) == 0, "Test-OE-Cluster not found"
        assert "Test-JF-Cluster" in unique_jf_clusters or len(df_enriched) == 0, "Test-JF-Cluster not found"
        
        print("[PASS] Clustering Workflow Test")
        
    finally:
        # Cleanup
        if os.path.exists(CLUSTER_FILE) and not has_backup:
             os.remove(CLUSTER_FILE)
             print("Removed test cluster file.")
        elif has_backup:
            if os.path.exists(CLUSTER_FILE):
                os.remove(CLUSTER_FILE)
            os.rename(backup_file, CLUSTER_FILE)
            print(f"Restored original cluster file from {backup_file}")

if __name__ == "__main__":
    test_clustering_workflow()
