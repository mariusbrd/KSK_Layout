from __future__ import annotations

import html

import streamlit as st

from config.settings import COLORS
from utils.i18n import t


def inject_ui_theme() -> None:
    """Inject the shared dashboard UI rules once per session."""
    if st.session_state.get("_dashboard_ui_theme_injected"):
        return

    st.session_state["_dashboard_ui_theme_injected"] = True
    st.markdown(
        f"""
        <style>
        :root {{
            --dashboard-accent: {COLORS["accent_blue"]};
            --dashboard-accent-light: {COLORS["accent_blue_light"]};
            --dashboard-border: #dce8f5;
            --dashboard-border-soft: #e2e8f0;
            --dashboard-surface: #ffffff;
            --dashboard-surface-soft: #f8fbff;
            --dashboard-surface-muted: #f8fafc;
            --dashboard-text: #0f172a;
            --dashboard-text-soft: #475569;
            --dashboard-text-muted: #64748b;
            --dashboard-radius: 14px;
            --dashboard-radius-sm: 12px;
            --dashboard-shadow: 0 6px 18px rgba(15, 23, 42, 0.05);
        }}

        section.main > div.block-container {{
            max-width: 100% !important;
            padding-top: 1.15rem !important;
            padding-bottom: 2rem !important;
            padding-left: 2.5rem !important;
            padding-right: 2.5rem !important;
        }}

        h1 {{
            color: var(--dashboard-text);
            letter-spacing: -0.01em;
            margin-bottom: 0.2rem !important;
        }}

        h2, h3 {{
            color: var(--dashboard-text);
            letter-spacing: -0.01em;
        }}

        div[data-testid="stCaptionContainer"] p {{
            color: var(--dashboard-text-muted);
            font-size: 0.91rem;
            line-height: 1.45;
        }}

        .dashboard-app-shell {{
            text-align: center;
            padding: 0.45rem 0 1.5rem 0;
        }}

        .dashboard-app-shell h1 {{
            color: #14b8a6;
            margin: 0 0 0.35rem 0;
            font-size: 2rem;
            line-height: 1.2;
        }}

        .dashboard-app-shell p {{
            color: #64748b;
            font-size: 1rem;
            margin: 0;
        }}

        .dashboard-page-header {{
            margin: 0.15rem 0 1.1rem 0;
        }}

        .dashboard-page-title {{
            color: var(--dashboard-text);
            font-size: 2rem;
            font-weight: 700;
            line-height: 1.15;
            margin: 0 0 0.3rem 0;
        }}

        .dashboard-page-subtitle {{
            color: var(--dashboard-text-muted);
            font-size: 0.98rem;
            line-height: 1.5;
            margin: 0;
            max-width: 78rem;
        }}

        .dashboard-page-note,
        .dashboard-context-box {{
            background: var(--dashboard-surface-soft);
            border: 1px solid var(--dashboard-border);
            border-left: 3px solid var(--dashboard-accent);
            border-radius: var(--dashboard-radius-sm);
            color: var(--dashboard-text-soft);
            line-height: 1.45;
        }}

        .dashboard-page-note {{
            margin-top: 0.8rem;
            padding: 0.78rem 0.95rem;
            font-size: 0.92rem;
        }}

        .dashboard-context-box {{
            margin: 0.15rem 0 1rem 0;
            padding: 0.78rem 0.95rem;
        }}

        .dashboard-context-box.compact {{
            margin: 0.2rem 0 0.9rem 0;
            padding: 0.65rem 0.85rem;
        }}

        .dashboard-context-box.warning {{
            background: #fffaf0;
            border-color: #fde7b1;
            border-left-color: {COLORS["accent_amber"]};
        }}

        .dashboard-context-box.success {{
            background: #f0fdf4;
            border-color: #bbf7d0;
            border-left-color: {COLORS["accent_green"]};
        }}

        .dashboard-context-box.neutral {{
            background: var(--dashboard-surface-muted);
            border-color: var(--dashboard-border-soft);
            border-left-color: #cbd5e1;
        }}

        .dashboard-context-label {{
            color: var(--dashboard-text-muted);
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin-bottom: 0.16rem;
        }}

        .dashboard-context-text {{
            color: var(--dashboard-text-soft);
            font-size: 0.91rem;
        }}

        .dashboard-section-intro {{
            margin: 1.15rem 0 0.75rem 0;
        }}

        .dashboard-section-title {{
            color: var(--dashboard-text);
            font-size: 1.08rem;
            font-weight: 700;
            line-height: 1.3;
            margin: 0 0 0.22rem 0;
        }}

        .dashboard-section-subtitle {{
            color: var(--dashboard-text-muted);
            font-size: 0.92rem;
            line-height: 1.45;
            margin: 0;
            max-width: 74rem;
        }}

        div[data-testid="stMetric"] {{
            background: linear-gradient(180deg, var(--dashboard-surface-soft) 0%, var(--dashboard-surface) 100%);
            border: 1px solid var(--dashboard-border);
            border-radius: var(--dashboard-radius);
            padding: 0.85rem 1rem;
            box-shadow: var(--dashboard-shadow);
        }}

        div[data-testid="stMetric"] label {{
            color: var(--dashboard-text-muted) !important;
            font-size: 0.78rem !important;
            font-weight: 700 !important;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }}

        div[data-testid="stMetricValue"] {{
            color: var(--dashboard-text) !important;
        }}

        div[data-testid="stMetricDelta"] {{
            font-size: 0.84rem !important;
        }}

        div[data-testid="stAlert"] {{
            border-radius: var(--dashboard-radius);
            border-width: 1px;
            box-shadow: none;
        }}

        div[data-testid="stAlert"] p {{
            font-size: 0.92rem;
            line-height: 1.45;
        }}

        div[data-testid="stExpander"] {{
            border: 1px solid var(--dashboard-border-soft);
            border-radius: var(--dashboard-radius);
            overflow: hidden;
            box-shadow: 0 4px 14px rgba(15, 23, 42, 0.03);
            margin-bottom: 0.65rem;
        }}

        div[data-testid="stExpander"] summary {{
            font-weight: 600;
        }}

        .stTabs [data-baseweb="tab-list"] {{
            gap: 0.35rem;
            margin-bottom: 0.35rem;
        }}

        .stTabs [data-baseweb="tab"] {{
            border-radius: 999px;
        }}

        section[data-testid="stSidebar"] .block-container {{
            padding-top: 0.9rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }}

        section[data-testid="stSidebar"] .e3rr4jk4 {{
            height: auto !important;
            min-height: 0 !important;
            align-items: flex-start !important;
            gap: 0.5rem;
            margin-bottom: 0.45rem !important;
        }}

        section[data-testid="stSidebar"] .e3rr4jk5,
        section[data-testid="stSidebar"] .e3rr4jk6,
        section[data-testid="stSidebar"] .e3rr4jk4 > div:first-child {{
            flex: 1 1 auto;
            max-width: calc(100% - 2.25rem);
        }}

        section[data-testid="stSidebar"] .e3rr4jk4 .stLogo {{
            display: block;
            width: 100% !important;
            max-width: 100% !important;
            height: auto !important;
            min-height: 4.75rem !important;
            max-height: none !important;
            object-fit: contain;
            object-position: left center;
        }}

        section[data-testid="stSidebar"] div:has(> [data-testid="stLogoLink"]),
        section[data-testid="stSidebar"] div:has(> div > [data-testid="stLogo"]) {{
            height: auto !important;
            min-height: 0 !important;
            align-items: flex-start !important;
            margin-bottom: 0.35rem !important;
        }}

        section[data-testid="stSidebar"] [data-testid="stLogoLink"] {{
            display: flex !important;
            align-items: center;
            width: 100% !important;
            max-width: 100% !important;
        }}

        section[data-testid="stSidebar"] [data-testid="stLogo"] {{
            display: block;
            width: 100% !important;
            max-width: 100% !important;
            height: auto !important;
            max-height: none !important;
            min-height: 4.5rem !important;
            margin-top: 0.15rem !important;
            margin-bottom: 0.15rem !important;
            object-fit: contain;
            object-position: left center;
        }}

        section[data-testid="stSidebar"] hr {{
            margin: 0.85rem 0;
        }}

        section[data-testid="stSidebar"] h2 {{
            color: var(--dashboard-text);
            font-size: 1.02rem;
            font-weight: 700;
            margin: 0.15rem 0 0.35rem 0;
        }}

        section[data-testid="stSidebar"] h3 {{
            color: var(--dashboard-text-soft);
            font-size: 0.76rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin: 0.72rem 0 0.38rem 0;
        }}

        section[data-testid="stSidebar"] div[data-testid="stCaptionContainer"] p {{
            font-size: 0.82rem;
            line-height: 1.35;
        }}

        .dashboard-sidebar-panel {{
            background: var(--dashboard-surface-muted);
            border: 1px solid var(--dashboard-border-soft);
            border-radius: var(--dashboard-radius);
            padding: 0.7rem 0.8rem;
            margin-bottom: 0.75rem;
        }}

        .dashboard-sidebar-panel-title {{
            color: var(--dashboard-text);
            font-size: 0.76rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin-bottom: 0.38rem;
        }}

        .dashboard-sidebar-status-row {{
            display: flex;
            align-items: center;
            gap: 0.42rem;
            color: var(--dashboard-text-soft);
            font-size: 0.8rem;
            line-height: 1.25;
            margin-bottom: 0.18rem;
        }}

        .dashboard-sidebar-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            flex-shrink: 0;
        }}

        .dashboard-sidebar-heading {{
            color: var(--dashboard-text);
            font-size: 1.02rem;
            font-weight: 700;
            margin: 0.1rem 0 0.3rem 0;
        }}

        .dashboard-sidebar-section {{
            color: var(--dashboard-text-soft);
            font-size: 0.76rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin: 0.72rem 0 0.38rem 0;
        }}

        .dashboard-sidebar-summary {{
            background: var(--dashboard-surface-soft);
            border: 1px solid var(--dashboard-border);
            border-radius: var(--dashboard-radius-sm);
            padding: 0.66rem 0.78rem;
            margin: 0.3rem 0 0.7rem 0;
            color: var(--dashboard-text-soft);
            font-size: 0.82rem;
            line-height: 1.35;
        }}

        .dashboard-sidebar-note {{
            background: #fffaf0;
            border: 1px solid #fde7b1;
            border-left: 3px solid {COLORS["accent_amber"]};
            border-radius: var(--dashboard-radius-sm);
            padding: 0.65rem 0.78rem;
            margin: 0.45rem 0 0.15rem 0;
            color: #7c5a10;
            font-size: 0.82rem;
            line-height: 1.35;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_app_shell_header(title: str, subtitle: str) -> None:
    inject_ui_theme()
    st.markdown(
        f"""
        <div class="dashboard-app-shell">
            <h1>{html.escape(title)}</h1>
            <p>{html.escape(subtitle)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(title: str, subtitle: str, note: str | None = None) -> None:
    inject_ui_theme()
    note_html = ""
    if note:
        note_html = f'<div class="dashboard-page-note">{html.escape(note)}</div>'

    st.markdown(
        f"""
        <div class="dashboard-page-header">
            <div class="dashboard-page-title">{html.escape(title)}</div>
            <div class="dashboard-page-subtitle">{html.escape(subtitle)}</div>
            {note_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_context_box(
    label: str,
    text: str,
    *,
    tone: str = "info",
    compact: bool = False,
) -> None:
    inject_ui_theme()
    classes = ["dashboard-context-box", tone]
    if compact:
        classes.append("compact")

    st.markdown(
        f"""
        <div class="{' '.join(classes)}">
            <div class="dashboard-context-label">{html.escape(label)}</div>
            <div class="dashboard-context-text">{html.escape(text)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_intro(title: str, subtitle: str | None = None) -> None:
    inject_ui_theme()
    subtitle_html = ""
    if subtitle:
        subtitle_html = f'<div class="dashboard-section-subtitle">{html.escape(subtitle)}</div>'

    st.markdown(
        f"""
        <div class="dashboard-section-intro">
            <div class="dashboard-section-title">{html.escape(title)}</div>
            {subtitle_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_active_filter_banner(filter_summary: str) -> None:
    """Render the active filter summary in the shared compact banner style."""
    inject_ui_theme()
    st.info(f"🎯 {t('ui.filter_banner.prefix')}: {filter_summary}")
