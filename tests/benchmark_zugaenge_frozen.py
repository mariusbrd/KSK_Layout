import copy
import json
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from tests.test_zugaenge_golden_master import _build_scenario
from zugaenge.forecast import run_forecast_zugaenge


def benchmark_zugaenge_frozen(runs: int = 7) -> dict:
    scenario = _build_scenario()

    # Warm-up run to avoid measuring one-time imports/cache setup.
    run_forecast_zugaenge(
        df_snapshot=scenario["snapshot_df"].copy(),
        start_date=scenario["start_date"],
        end_date=scenario["end_date"],
        freq="M",
        params=copy.deepcopy(scenario["params"]),
        vacancies=scenario["vacancies_df"].to_dict("records"),
    )

    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        run_forecast_zugaenge(
            df_snapshot=scenario["snapshot_df"].copy(),
            start_date=scenario["start_date"],
            end_date=scenario["end_date"],
            freq="M",
            params=copy.deepcopy(scenario["params"]),
            vacancies=scenario["vacancies_df"].to_dict("records"),
        )
        samples.append(time.perf_counter() - start)

    return {
        "runs": runs,
        "samples": [round(value, 4) for value in samples],
        "median": round(statistics.median(samples), 4),
        "min": round(min(samples), 4),
    }


def main():
    print(json.dumps(benchmark_zugaenge_frozen(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
