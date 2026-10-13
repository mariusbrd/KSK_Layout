"""Shared age inputs and explanation for the existing retirement controls."""
from typing import Any

import streamlit as st

from abgaenge.params import get_retirement_ages
from utils.i18n import t


def render_retirement_age_inputs(retirement: dict[str, Any], *, key_prefix: str) -> tuple[int, int]:
    regular_col, early_col = st.columns(2)
    with regular_col:
        regular = st.number_input(t("attrition.retirement.regular_age"), min_value=1, max_value=120,
                                  value=int(retirement.get("retirement_age", 65)), step=1,
                                  key=f"{key_prefix}_retirement_age")
    with early_col:
        early = st.number_input(t("attrition.retirement.early_age"), min_value=1, max_value=120,
                                value=int(retirement.get("early_retirement_age", 60)), step=1,
                                key=f"{key_prefix}_early_retirement_age",
                                help=t("attrition.retirement.early_age.help"))
    try:
        return get_retirement_ages({"retirement_age": regular, "early_retirement_age": early})
    except ValueError:
        st.error(t("attrition.retirement.invalid_ages"))
        st.stop()


def render_retirement_summary(regular: int, early: int, regular_rate: float, early_rate: float) -> None:
    st.caption(t("attrition.retirement.summary", early=early, upper=regular - 1, regular=regular,
                 early_rate=f"{early_rate * 100:g}", regular_rate=f"{regular_rate * 100:g}"))
