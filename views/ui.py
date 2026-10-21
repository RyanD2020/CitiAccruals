"""Shared Streamlit helpers: one cached service, one cached data load, user identity and styling."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import engine
from core.config import load_settings
from core.db import make_backend
from core.matcher import GREEN, MANUAL, PRIOR, RED, YELLOW
from core.services import Service
from core.storage import FileStore

DARK_BLUE, BLUE, GREEN_HEX, LIGHT_GREEN = "#000A62", "#0468FF", "#016355", "#A5EFBF"
ORANGE, LIGHT_ORANGE, GREY = "#FF9522", "#FFE8CF", "#E3E0DD"
# Red is not in the NYL palette; it is used only as the "needs a decision" signal the business asked for.
RED_TEXT, RED_BG = "#9B111E", "#FBE3E4"

RESULT_LABEL = {GREEN: "Match", YELLOW: "Review", RED: "No match", MANUAL: "Manual match", PRIOR: "Before tracking"}
RESULT_STYLE = {
    "Match": f"background-color: {LIGHT_GREEN}; color: {GREEN_HEX}; font-weight: 600",
    "Review": f"background-color: {LIGHT_ORANGE}; color: #7A4300; font-weight: 600",
    "No match": f"background-color: {RED_BG}; color: {RED_TEXT}; font-weight: 600",
    "Manual match": f"background-color: #DCEBFF; color: {DARK_BLUE}; font-weight: 600",
    "Before tracking": f"background-color: #F2F2F2; color: #444444",
}
STATUS_STYLE = {
    engine.STATUS_PAID: f"background-color: {LIGHT_GREEN}; color: {GREEN_HEX}",
    engine.STATUS_APPROVED: f"background-color: #DCEBFF; color: {DARK_BLUE}",
    engine.STATUS_INVOICED: f"background-color: #DCEBFF; color: {DARK_BLUE}",
    engine.STATUS_DUE: f"background-color: {LIGHT_ORANGE}; color: #7A4300",
    engine.STATUS_NOT_DUE: "color: #555555",
    engine.STATUS_WRITTEN_OFF: f"background-color: #F2F2F2; color: #444444; text-decoration: line-through",
}

CSS = f"""
<style>
html, body, [class*="css"], .stMarkdown, .stText, p, li, label, input, textarea, button {{
  font-family: Arial, Helvetica, "Liberation Sans", sans-serif;
}}
h1, h2, h3, h4, [data-testid="stHeading"] * {{
  font-family: Georgia, "Times New Roman", serif !important; color: {DARK_BLUE} !important; font-weight: 400 !important;
}}
h1 {{ font-size: 2.2rem !important; letter-spacing: -0.01em; }}
.nyl-eyebrow {{ color: {BLUE}; font-family: Georgia, serif; font-size: 1.05rem; margin-bottom: -0.6rem; }}
.nyl-lede {{ color: #000; max-width: 68ch; margin-top: -0.3rem; }}
[data-testid="stMetricValue"] {{ font-family: Georgia, serif; color: {DARK_BLUE}; }}
[data-testid="stMetricLabel"] p {{ color: #000; }}
.nyl-chip {{ display:inline-block; padding: 0.15rem 0.55rem; border-radius: 0.8rem; font-size: 0.85rem; margin-right: 0.35rem; }}
.nyl-green {{ background:{LIGHT_GREEN}; color:{GREEN_HEX}; }}
.nyl-yellow {{ background:{LIGHT_ORANGE}; color:#7A4300; }}
.nyl-red {{ background:{RED_BG}; color:{RED_TEXT}; }}
.nyl-blue {{ background:#DCEBFF; color:{DARK_BLUE}; }}
.nyl-big {{ font-family: Georgia, serif; color:{DARK_BLUE}; font-size: 3rem; line-height: 1.05; }}
.nyl-big-label {{ color:#000; font-size: 0.95rem; }}
a {{ color: {BLUE}; }} a:hover {{ color: {DARK_BLUE}; }}
</style>
"""


@st.cache_resource(show_spinner="Connecting to the accrual tables...")
def service() -> Service:
    s = load_settings()
    db = make_backend(s)
    db.ensure_schema()
    svc = Service(db, FileStore(s), s)
    svc.ensure_defaults("system")
    return svc


@st.cache_data(ttl=600, show_spinner="Loading ledger...")
def _load() -> dict[str, pd.DataFrame]:
    return service().load()


def data() -> dict[str, pd.DataFrame]:
    return _load()


def refresh() -> None:
    """Call after every write so all users see the change."""
    _load.clear()


def user() -> str:
    try:
        h = st.context.headers
        for k in ("X-Forwarded-Email", "X-Forwarded-Preferred-Username", "X-Forwarded-User"):
            if h.get(k):
                return h.get(k)
    except Exception:
        pass
    import getpass
    return f"{getpass.getuser()} (local)"


def user_display() -> str:
    u = user()
    name = u.split("@")[0].replace(".", " ").title() if "@" in u else u
    return name


def header(title: str, lede: str | None = None, eyebrow: str | None = None) -> None:
    if eyebrow:
        st.markdown(f'<div class="nyl-eyebrow">{eyebrow}</div>', unsafe_allow_html=True)
    st.title(title)
    if lede:
        st.markdown(f'<p class="nyl-lede">{lede}</p>', unsafe_allow_html=True)


def money(v) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return ""


def chip(text: str, kind: str) -> str:
    return f'<span class="nyl-chip nyl-{kind}">{text}</span>'


def style_col(df: pd.DataFrame, col: str, mapping: dict, money_cols=(), date_cols=()):
    fmt = {c: (lambda v: money(v) if pd.notna(v) else "") for c in money_cols if c in df.columns}
    fmt.update({c: (lambda v: v.strftime("%d-%b-%Y") if hasattr(v, "strftime") else ("" if v is None or (isinstance(v, float) and pd.isna(v)) else v))
                for c in date_cols if c in df.columns})
    sty = df.style.format(fmt, na_rep="")
    if col in df.columns:
        sty = sty.map(lambda v: mapping.get(v, ""), subset=[col])
    return sty


def ledger(as_of: date) -> engine.Ledger:
    d = data()
    return engine.ledger(d["fee_events"], d["contracts"], d["invoices"], d["programs"], as_of)


def require_data() -> bool:
    if data()["contracts"].empty:
        st.info("No data yet. Load your existing folder on the Setup page to get started.")
        st.page_link("views/setup.py", label="Go to Setup")
        return False
    return True


def event_label(r) -> str:
    g = r.get if isinstance(r, dict) else (lambda k, d=None: getattr(r, k, d))
    return (f"{g('series')}, FA {int(g('fa_number'))}, due {g('event_date'):%d-%b-%Y}, "
            f"{money(g('expected_amount'))}")


def reason_box(key: str, placeholder: str = "Why are you making this change?") -> str:
    return st.text_input("Reason (saved to the audit log)", key=key, placeholder=placeholder)
