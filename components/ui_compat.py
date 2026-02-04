"""UI compatibility helpers for Streamlit width handling."""

from __future__ import annotations

import inspect
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
