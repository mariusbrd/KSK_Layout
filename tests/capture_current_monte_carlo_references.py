"""One-time capture for the cluster mapping stored at calculation baseline 044bdae."""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_monte_carlo_production_multi_year_reference import _payload as multi_payload
from test_monte_carlo_seed_reference import CASES, _payload as seed_payload

EXPECTED_MAPPING_SHA256 = "52d18a2dbb46f8f29f2567f1caa3574096d203d0cb9b4481a78d3cc718656625"
PRODUCT_PATHS = [
    "dataloader/compact_simulation_engine.py", "abgaenge/forecast.py",
    "zugaenge/forecast.py", "components/jobfamily_simulation_yearly.py",
    "utils/compact_regular_stock.py", "utils/org_simulation_events.py",
    "config/cluster_mapping.xlsx",
]


def main() -> None:
    mapping_hash = hashlib.sha256((ROOT / "config" / "cluster_mapping.xlsx").read_bytes()).hexdigest()
    if mapping_hash != EXPECTED_MAPPING_SHA256:
        raise RuntimeError("Cluster mapping differs from the certified calculation baseline")
    unchanged = subprocess.run(
        ["git", "diff", "--quiet", "monte-carlo-calculation-v1", "--", *PRODUCT_PATHS],
        cwd=ROOT, check=False)
    if unchanged.returncode:
        raise RuntimeError("Product calculation paths differ from calculation baseline")
    multi_file = ROOT / "tests" / "fixtures" / "monte_carlo_production_multi_year_reference_v5.json"
    seed_file = ROOT / "tests" / "fixtures" / "monte_carlo_seed_reference_v2.json"
    if multi_file.exists() or seed_file.exists():
        raise RuntimeError("Current-mapping reference already exists; refusing to overwrite")
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore", category=FutureWarning)
    multi = {"description": "Hash-only product reference for current cluster mapping",
             "source_tag": "monte-carlo-calculation-v1",
             "cluster_mapping_sha256": mapping_hash,
             "payload": multi_payload()}
    print("Captured current-mapping multi-year reference", flush=True)
    seeds = {"description": "Hash-only seeded OE/JF references for current cluster mapping",
             "source_tag": "monte-carlo-calculation-v1",
             "cluster_mapping_sha256": mapping_hash,
             "cases": {f"{scenario}:{seed}": seed_payload(scenario, seed)
                       for scenario, seed in CASES}}
    multi_file.write_text(json.dumps(multi, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    seed_file.write_text(json.dumps(seeds, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    print("Captured current-mapping seed reference", flush=True)


if __name__ == "__main__":
    main()
