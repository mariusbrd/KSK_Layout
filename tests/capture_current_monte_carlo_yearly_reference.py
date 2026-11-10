"""One-time hash-only yearly reference for the current certified cluster file."""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import warnings

from capture_current_monte_carlo_references import (
    EXPECTED_MAPPING_SHA256, PRODUCT_PATHS, ROOT,
)
from test_monte_carlo_production_yearly_reference import _payload, CURRENT_REFERENCE


def main() -> None:
    mapping_hash = hashlib.sha256((ROOT / "config" / "cluster_mapping.xlsx").read_bytes()).hexdigest()
    if mapping_hash != EXPECTED_MAPPING_SHA256:
        raise RuntimeError("Cluster mapping differs from certified calculation baseline")
    unchanged = subprocess.run(
        ["git", "diff", "--quiet", "monte-carlo-calculation-v1", "--", *PRODUCT_PATHS],
        cwd=ROOT, check=False)
    if unchanged.returncode:
        raise RuntimeError("Product calculation paths differ from calculation baseline")
    if CURRENT_REFERENCE.exists():
        raise RuntimeError("Current yearly reference already exists; refusing overwrite")
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore", category=FutureWarning)
    reference = {
        "description": "Hash-only yearly product reference for current cluster mapping",
        "source_tag": "monte-carlo-calculation-v1",
        "cluster_mapping_sha256": mapping_hash,
        "payload": _payload(),
    }
    CURRENT_REFERENCE.write_text(json.dumps(reference, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    print(f"Captured {CURRENT_REFERENCE.name}")


if __name__ == "__main__":
    main()
