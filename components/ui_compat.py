"""UI compatibility helpers for Streamlit width handling."""

from __future__ import annotations

import inspect
import base64
import json
from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st


def _supports_kw(func, kw: str) -> bool:
    try:
        return kw in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def _coerce_container_width(func, kwargs: dict) -> dict:
    # Convert width="stretch" to use_container_width=True where supported.
    if kwargs.get("width") == "stretch":
        kwargs.pop("width", None)
        if _supports_kw(func, "use_container_width"):
            kwargs["use_container_width"] = True
    return kwargs


def dataframe_compat(data, **kwargs):
    kwargs = _coerce_container_width(st.dataframe, dict(kwargs))
    if not _supports_kw(st.dataframe, "use_container_width"):
        kwargs.pop("use_container_width", None)
    return st.dataframe(data, **kwargs)


def button_compat(label, **kwargs):
    kwargs = _coerce_container_width(st.button, dict(kwargs))
    if not _supports_kw(st.button, "use_container_width"):
        kwargs.pop("use_container_width", None)
    return st.button(label, **kwargs)


def download_button_compat(label, **kwargs):
    kwargs = _coerce_container_width(st.download_button, dict(kwargs))
    if not _supports_kw(st.download_button, "use_container_width"):
        kwargs.pop("use_container_width", None)
    return st.download_button(label, **kwargs)


def dataframe_export_fingerprint(data: pd.DataFrame, *parts: Any) -> tuple[Any, ...]:
    """Cheaply identify the currently visible export payload for lazy downloads."""
    columns = tuple(str(col) for col in data.columns)
    dtypes = tuple(str(dtype) for dtype in data.dtypes)
    hash_source = data if len(data) <= 500 else pd.concat([data.head(50), data.tail(50)])
    try:
        values_hash = int(pd.util.hash_pandas_object(hash_source, index=True).sum())
    except Exception:
        values_hash = hash(tuple(hash_source.astype(str).to_numpy().ravel()))
    return (tuple(parts), data.shape, columns, dtypes, values_hash)


def _trigger_browser_download(data: bytes, *, file_name: str, mime: str, nonce: int) -> None:
    encoded = base64.b64encode(data).decode("ascii")
    st.components.v1.html(
        f"""
        <script>
        (() => {{
            const targetDocument = (() => {{
                try {{
                    return window.parent && window.parent.document
                        ? window.parent.document
                        : document;
                }} catch (_) {{
                    return document;
                }}
            }})();
            const link = targetDocument.createElement("a");
            link.href = "data:" + {json.dumps(mime)} + ";base64," + {json.dumps(encoded)};
            link.download = {json.dumps(file_name)};
            link.dataset.downloadNonce = {json.dumps(str(nonce))};
            targetDocument.body.appendChild(link);
            link.click();
            link.remove();
        }})();
        </script>
        """,
        height=0,
    )


def lazy_excel_download_button_compat(
    *,
    label: str,
    data_builder: Callable[[], bytes],
    file_name: str,
    mime: str,
    key: str,
    spinner_label: str = "Excel-Export wird erstellt...",
    fingerprint: Any | None = None,
    **kwargs,
):
    """Build and trigger Excel downloads only after the visible button is clicked.

    Streamlit needs concrete bytes for st.download_button during rendering. This
    helper keeps expensive Excel/Lineage generation out of the normal page load
    while preserving a single visible export button.
    """
    fingerprint_key = f"{key}__lazy_fingerprint"
    payload_key = f"{key}__lazy_payload"
    error_key = f"{key}__lazy_error"
    nonce_key = f"{key}__lazy_nonce"

    if fingerprint is not None and st.session_state.get(fingerprint_key) != fingerprint:
        st.session_state.pop(error_key, None)
        st.session_state.pop(payload_key, None)
        st.session_state[fingerprint_key] = fingerprint

    button_kwargs = dict(kwargs)
    button_kwargs["key"] = key
    if button_compat(label, **button_kwargs):
        try:
            payload = st.session_state.get(payload_key) if fingerprint is not None else None
            if payload is None:
                with st.spinner(spinner_label):
                    payload = data_builder()
                if fingerprint is not None:
                    st.session_state[payload_key] = payload
            nonce = int(st.session_state.get(nonce_key, 0) or 0) + 1
            st.session_state[nonce_key] = nonce
            _trigger_browser_download(
                payload,
                file_name=file_name,
                mime=mime,
                nonce=nonce,
            )
            st.session_state.pop(error_key, None)
            return True
        except Exception as exc:  # pragma: no cover - surfaced in Streamlit UI
            st.session_state[error_key] = str(exc)

    if st.session_state.get(error_key):
        st.error(f"Excel-Export konnte nicht erstellt werden: {st.session_state[error_key]}")

    return False


def ensure_iframe_compat() -> None:
    """Restore st.iframe for third-party components (e.g. streamlit-scroll-navigation)
    that still call it directly. st.iframe was removed from newer Streamlit versions;
    st.components.v1.html is the current equivalent for rendering raw HTML/JS content
    (as opposed to st.components.v1.iframe, which loads a URL as src, not raw markup)."""
    if not hasattr(st, "iframe"):
        st.iframe = st.components.v1.html
