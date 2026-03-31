import io
import json
import os
from typing import Any, Dict, Optional, Tuple

import streamlit as st


def _normalize_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_for_json(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_json(v) for v in value]
    if isinstance(value, set):
        return sorted(_normalize_for_json(v) for v in value)
    return value


def stable_json_dumps(value: Any) -> str:
    return json.dumps(_normalize_for_json(value), sort_keys=True, ensure_ascii=False, default=str)


def get_file_signature(path: Optional[str]) -> Optional[Tuple[str, int, int]]:
    if not path or not os.path.exists(path):
        return None
    stat = os.stat(path)
    return (os.path.abspath(path), int(stat.st_mtime_ns), int(stat.st_size))


def coerce_file_bytes(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if hasattr(value, "getvalue"):
        return value.getvalue()
    if hasattr(value, "read"):
        pos = value.tell() if hasattr(value, "tell") else None
        data = value.read()
        if pos is not None and hasattr(value, "seek"):
            value.seek(pos)
        return data
    raise TypeError(f"Unsupported upload value type: {type(value)!r}")


def ensure_file_like(value: Any) -> Any:
    data = coerce_file_bytes(value)
    if data is None:
        return None
    return io.BytesIO(data)


def serialize_uploaded_files(uploaded_files: Optional[Dict[str, Any]]) -> Tuple[Tuple[str, bytes], ...]:
    if not uploaded_files:
        return tuple()

    payload = []
    for key in sorted(uploaded_files):
        data = coerce_file_bytes(uploaded_files[key])
        if data is not None:
            payload.append((str(key), data))
    return tuple(payload)


def deserialize_uploaded_files(payload: Tuple[Tuple[str, bytes], ...]) -> Dict[str, io.BytesIO]:
    return {key: io.BytesIO(data) for key, data in payload}


def get_cache_version(namespace: str) -> int:
    return int(st.session_state.get(f"_cache_version_{namespace}", 0))


def bump_cache_version(namespace: str) -> int:
    key = f"_cache_version_{namespace}"
    next_value = int(st.session_state.get(key, 0)) + 1
    st.session_state[key] = next_value
    return next_value


def ensure_cache_bootstrap(namespace: str, token: str) -> None:
    key = f"_cache_bootstrap_{namespace}"
    if st.session_state.get(key) != token:
        bump_cache_version(namespace)
        st.session_state[key] = token
