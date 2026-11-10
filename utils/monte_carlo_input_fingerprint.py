"""Process-stable fingerprint of the immutable inputs to a Monte Carlo run."""
from __future__ import annotations

import base64
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd


FINGERPRINT_VERSION = 1


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _normalized(value: Any) -> Any:
    """Encode supported values without depending on process hash randomization."""
    if value is None or value is pd.NA or value is pd.NaT:
        return ["missing"]
    if isinstance(value, np.generic):
        return _normalized(value.item())
    if isinstance(value, pd.DataFrame):
        return ["dataframe", frame_fingerprint(value)]
    if isinstance(value, pd.Series):
        return ["series", str(value.name), frame_fingerprint(value.to_frame())]
    if isinstance(value, Mapping):
        items = [(_normalized(key), _normalized(item)) for key, item in value.items()]
        return ["mapping", sorted(items, key=lambda pair: _json(pair[0]))]
    if isinstance(value, np.ndarray):
        return ["array", str(value.dtype), list(value.shape), _normalized(value.tolist())]
    if isinstance(value, tuple):
        return ["tuple", [_normalized(item) for item in value]]
    if isinstance(value, list):
        return ["list", [_normalized(item) for item in value]]
    if isinstance(value, (set, frozenset)):
        return ["set", sorted((_normalized(item) for item in value), key=_json)]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return ["date", value.isoformat()]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if math.isnan(value):
            return ["nan"]
        return ["float", value.hex()]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    raise TypeError(f"Unsupported fingerprint value: {type(value).__name__}")


def frame_fingerprint(frame: pd.DataFrame) -> str:
    """Hash schema, index and ordered cell values, including nested object cells."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Expected a DataFrame")
    digest = hashlib.sha256()
    schema = {
        "version": FINGERPRINT_VERSION,
        "shape": frame.shape,
        "columns": [(_normalized(name), repr(dtype))
                    for name, dtype in zip(frame.columns, frame.dtypes)],
        "index_names": [_normalized(name) for name in frame.index.names],
        "index_dtype": repr(getattr(frame.index, "dtype", None)),
    }
    digest.update(_json(schema).encode("utf-8"))
    hashable = frame.copy(deep=False)
    object_columns = [position for position, dtype in enumerate(frame.dtypes)
                      if pd.api.types.is_object_dtype(dtype)]
    if object_columns:
        hashable = frame.copy()
        for position in object_columns:
            hashable.iloc[:, position] = frame.iloc[:, position].map(
                lambda value: _json(_normalized(value)))
    row_hashes = pd.util.hash_pandas_object(hashable, index=True,
                                             categorize=False).to_numpy(dtype="uint64")
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


def fingerprint_simulation_inputs(snapshot: pd.DataFrame, atz: pd.DataFrame, *,
                                  salary_automation: Any = None,
                                  cost_lookup: Any = None,
                                  cluster_mapping: Any = None) -> str:
    """Cover product data and auxiliary inputs that can affect a worker run.

    Forecast parameters, dates, exclusions and cluster source signature belong
    to the separate RunPlan signature and must also be checked by each worker.
    """
    payload = {
        "version": FINGERPRINT_VERSION,
        "snapshot": frame_fingerprint(snapshot),
        "atz": frame_fingerprint(atz),
        "salary_automation": _normalized(salary_automation),
        "cost_lookup": _normalized(cost_lookup),
        "cluster_mapping": _normalized(cluster_mapping),
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
