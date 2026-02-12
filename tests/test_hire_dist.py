
import pandas as pd
import numpy as np
import sys
import os
from typing import Dict, Any

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zugaenge.forecast import _simulate_hires, PeriodInfo

def test_hire_distribution():
    print("--- Testing New Hire Distribution Logic ---")
    
    rng = np.random.RandomState(42)
    period = PeriodInfo(
        start=pd.Timestamp("2025-01-01"),
        end=pd.Timestamp("2025-12-31"),
        label="2025"
    )
    
    # 1. Test Vacancy Inheritance
    print("\n[Test 1] Vacancy Inheritance")
    vacancies = [{
        "date": pd.Timestamp("2025-01-01"), # Set to start of year to match any entry date
        "org_unit": "Org_A",
        "planstelle": "SpecificPos",
        "persnr": "123",
        "Jobfamily": "UniqueJF",    # Should be inherited
        "OE-Cluster": "UniqueCluster" # Should be inherited
    }]
    
    params_vac = {
        "new_hires": {
            "count_per_year": 1,
            "strategy": "Fill Vacancies"
        }
    }
    
    events_1 = []
    _simulate_hires(pd.DataFrame(), params_vac, period, rng, events_1, ["Org_A"], vacancies)
    
    if events_1:
        e = events_1[0]
        print(f"  New Hire JF: {e.get('Jobfamily')}, Cluster: {e.get('OE-Cluster')}")
        if e.get("Jobfamily") == "UniqueJF" and e.get("OE-Cluster") == "UniqueCluster":
             print("  [SUCCESS] Attributes inherited from vacancy.")
        else:
             print("  [FAILURE] Attributes NOT inherited.")
    else:
        print("  [FAILURE] No event generated.")

    # 2. Test Matrix Distribution
    print("\n[Test 2] Matrix Distribution (No Vacancies)")
    
    dist = [
        {"Jobfamily": "Type_A", "OE-Cluster": "Clust_A", "Share %": 1.0}
    ]
    
    params_mat = {
        "new_hires": {
            "count_per_year": 5,
            "strategy": "Random", # Not Fill Vacancies
            "distribution": dist
        }
    }
    
    events_2 = []
    _simulate_hires(pd.DataFrame(), params_mat, period, rng, events_2, ["Org_B"], [])
    
    success_cnt = 0
    for e in events_2:
        if e.get("Jobfamily") == "Type_A" and e.get("OE-Cluster") == "Clust_A":
            success_cnt += 1
            
    print(f"  Generated {len(events_2)} events. {success_cnt} matching Matrix.")
    
    if success_cnt == len(events_2) and len(events_2) > 0:
        print("  [SUCCESS] All new hires followed the Matrix.")
    else:
        print("  [FAILURE] Matrix distribution failed.")

if __name__ == "__main__":
    test_hire_distribution()
