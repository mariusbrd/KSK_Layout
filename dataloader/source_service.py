import os
from enum import Enum
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import streamlit as st

from dataloader.cluster_resolver import (
    DEFAULT_EXTERNAL_FILE_PATH,
    DEFAULT_PERSISTED_LOCAL_PATH,
    MODE_INPUT_FOLDER,
    MODE_SYNTHETIC,
    MODE_UI_UPLOAD,
    get_active_cluster_source,
    get_active_cluster_source_from_session,
)

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
    def derive_cluster_group_status(
        *,
        active_cluster_source=None,
        session_state=None,
        cluster_dir: Optional[str] = None,
        persisted_local_path: Optional[str] = None,
    ) -> GroupStatus:
        if active_cluster_source is None:
            session = session_state if session_state is not None else st.session_state
            active_cluster_source = get_active_cluster_source_from_session(session)
            if active_cluster_source is None:
                external_file_path = (
                    os.path.join(cluster_dir, "OE_Cluster.xlsx")
                    if cluster_dir
                    else DEFAULT_EXTERNAL_FILE_PATH
                )
                active_cluster_source = get_active_cluster_source(
                    session_state=session,
                    persisted_local_path=persisted_local_path or DEFAULT_PERSISTED_LOCAL_PATH,
                    external_file_path=external_file_path,
                )

        if active_cluster_source.mode == MODE_UI_UPLOAD:
            return GroupStatus(
                group_name="Clusterinformationen",
                origin=DataSourceOrigin.UPLOADED,
                sources=[active_cluster_source.display_label or active_cluster_source.subtype],
                is_complete=bool(active_cluster_source.is_valid),
                completeness_label="aktiv" if active_cluster_source.is_valid else "ungültig",
            )

        if active_cluster_source.mode == MODE_INPUT_FOLDER:
            return GroupStatus(
                group_name="Clusterinformationen",
                origin=DataSourceOrigin.ORIGINAL,
                sources=[active_cluster_source.display_label or active_cluster_source.subtype],
                is_complete=bool(active_cluster_source.is_valid),
                completeness_label="aktiv" if active_cluster_source.is_valid else "ungültig",
            )

        return GroupStatus(
            group_name="Clusterinformationen",
            origin=DataSourceOrigin.SYNTHETIC,
            sources=[active_cluster_source.display_label or active_cluster_source.subtype],
            is_complete=True,
            completeness_label="aktiv",
        )

    @staticmethod
    def derive_group_status(
        group_name: str, 
        uploads: Dict[str, Any], 
        original_dir: str,
        cluster_dir: str,
        *,
        active_cluster_source=None,
        session_state=None,
        persisted_local_path: Optional[str] = None,
    ) -> GroupStatus:
        if group_name == "Clusterinformationen":
            return SourceService.derive_cluster_group_status(
                active_cluster_source=active_cluster_source,
                session_state=session_state,
                cluster_dir=cluster_dir,
                persisted_local_path=persisted_local_path,
            )

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
