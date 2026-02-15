import os
from enum import Enum
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

class DataSourceOrigin(Enum):
    UPLOADED = "vom Nutzer hochgeladen"
    ORIGINAL = "Originaldaten"
    SYNTHETIC = "synthetisch erzeugt"

@dataclass
class GroupStatus:
    group_name: str
    origin: DataSourceOrigin
    sources: List[str]
    is_complete: bool
    completeness_label: str

class SourceService:
    GROUPS = {
        "Mitarbeiterinformationen": [
            "Mitarbeiter.xlsx",
            "Planstellen.XLSX",
            "ATZ.xlsx",
            "Ausbildung.xlsx"
        ],
        "Entgeltinformationen": [
            "TVÖD.xlsx"
        ],
        "Clusterinformationen": [
            "OE_Cluster.xlsx"
        ]
    }

    # Internal mapping for uploads (keys in session_state["global_uploads"])
    UPLOAD_KEYS = {
        "Mitarbeiter.xlsx": "Mitarbeiter",
        "Planstellen.XLSX": "Planstellen",
        "ATZ.xlsx": "ATZ",
        "Ausbildung.xlsx": "Ausbildung",
        "TVÖD.xlsx": "TVÖD",
        "OE_Cluster.xlsx": "Cluster"
    }

    @staticmethod
    def derive_group_status(
        group_name: str, 
        uploads: Dict[str, Any], 
        original_dir: str,
        cluster_dir: str
    ) -> GroupStatus:
        relevant_files = SourceService.GROUPS.get(group_name, [])
        
        # 1. Check for Uploads
        uploaded_files = []
        for f in relevant_files:
            upload_key = SourceService.UPLOAD_KEYS.get(f)
            if upload_key in uploads and uploads[upload_key] is not None:
                uploaded_files.append(f)
        
        if len(uploaded_files) > 0:
            is_complete = len(uploaded_files) == len(relevant_files)
            label = "vollständig" if is_complete else "teilweise vorhanden"
            return GroupStatus(
                group_name=group_name,
                origin=DataSourceOrigin.UPLOADED,
                sources=relevant_files,
                is_complete=is_complete,
                completeness_label=label
            )

        # 2. Check for Original Data
        original_found = []
        for f in relevant_files:
            # Cluster files might be in a different dir
            search_dir = cluster_dir if group_name == "Clusterinformationen" else original_dir
            path = os.path.join(search_dir, f)
            
            # Robust check (case insensitive for extension)
            if os.path.exists(path):
                original_found.append(f)
            else:
                # Try .xlsx if .XLSX or vice versa
                alt_f = f.replace(".xlsx", ".XLSX") if ".xlsx" in f else f.replace(".XLSX", ".xlsx")
                if os.path.exists(os.path.join(search_dir, alt_f)):
                    original_found.append(f)
        
        if len(original_found) > 0:
            is_complete = len(original_found) == len(relevant_files)
            label = "vollständig" if is_complete else "teilweise vorhanden"
            return GroupStatus(
                group_name=group_name,
                origin=DataSourceOrigin.ORIGINAL,
                sources=relevant_files,
                is_complete=is_complete,
                completeness_label=label
            )

        # 3. Fallback to Synthetic
        return GroupStatus(
            group_name=group_name,
            origin=DataSourceOrigin.SYNTHETIC,
            sources=relevant_files,
            is_complete=True, # Synthetic is always "complete" in terms of working
            completeness_label="aktiv"
        )
