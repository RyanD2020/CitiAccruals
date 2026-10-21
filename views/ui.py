"""Shared Streamlit helpers: one cached service, one cached data load, user identity and the New York Life design.

The design lives in design/nyl.css (rules in design/BRAND.md); app.py injects it once per run with css().
The helpers below render branded HTML with st.html, which sanitises it (no scripts, no SVG).
"""
from __future__ import annotations

import base64
import html as _html
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st

from core import engine
from core.config import load_settings
from core.dates import last_completed_month_end
from core.db import make_backend
from core.matcher import GREEN, MANUAL, PRIOR, RED, YELLOW
from core.services import Service
from core.storage import FileStore

ROOT = Path(__file__).resolve().parent.parent
DESIGN_CSS = ROOT / "design" / "nyl.css"
LOGO = ROOT / "assets" / "nyl_logo.png"

DARK_BLUE, BLUE, GREEN_HEX, LIGHT_GREEN, GREY = "#000A62", "#0468FF", "#016355", "#A5EFBF", "#E3E0DD"
AXIS_GREY = "#888888"

# Status chip colours (design/BRAND.md): kind -> (background, text)
CHIP_COLORS = {
    "pass": ("#A5EFBF", "#016355"),
    "review": ("#FFE8CF", "#6B3B00"),
    "fail": ("#FBE3E4", "#9B111E"),
    "info": ("#DCEBFF", "#000A62"),
}
CHIP_KINDS = {"pass", "match", "review", "fail", "nomatch", "info", "approved", "manual", "plain"}


def cell_style(kind: str) -> str:
    """Styler CSS for a dataframe cell shown as a chip colour ('' for plain)."""
    if kind not in CHIP_COLORS:
        return ""
    bg, fg = CHIP_COLORS[kind]
    return f"background-color: {bg}; color: {fg}; font-weight: 600"


RESULT_LABEL = {GREEN: "Match", YELLOW: "Review", RED: "No match", MANUAL: "Manual match", PRIOR: "Before tracking"}
RESULT_KIND = {"Match": "match", "Review": "review", "No match": "nomatch", "Manual match": "manual",
               "Before tracking": "plain"}
RESULT_STYLE = {
    "Match": cell_style("pass"), "Review": cell_style("review"), "No match": cell_style("fail"),
    "Manual match": cell_style("info"), "Before tracking": "",
}
STATUS_KIND = {
    engine.STATUS_PAID: "pass", engine.STATUS_APPROVED: "approved", engine.STATUS_INVOICED: "info",
    engine.STATUS_DUE: "review", engine.STATUS_NOT_DUE: "plain", engine.STATUS_WRITTEN_OFF: "plain",
}
STATUS_STYLE = {
    engine.STATUS_PAID: cell_style("pass"), engine.STATUS_APPROVED: cell_style("info"),
    engine.STATUS_INVOICED: cell_style("info"), engine.STATUS_DUE: cell_style("review"),
    engine.STATUS_NOT_DUE: "", engine.STATUS_WRITTEN_OFF: "",
}


# ------------------------------------------------------------------ design
@lru_cache(maxsize=1)
def _design_source() -> str:
    return DESIGN_CSS.read_text(encoding="utf-8")


def _token_block(css: str, selector: str) -> str:
    m = re.search(r"(?m)^" + re.escape(selector) + r" \{(.*?)^\}", css, re.S)
    return m.group(1) if m else ""


def css() -> str:
    """design/nyl.css with its colour tokens pinned to the Streamlit theme the user picked (light or dark).

    nyl.css on its own follows the operating system's preference; inside the app the Streamlit theme decides,
    so a user who picks dark in the settings menu gets dark tokens even on a light OS, and vice versa.
    """
    src = _design_source()
    dark = getattr(st.context.theme, "type", None) == "dark"
    tokens = _token_block(src, ':root[data-theme="dark"]' if dark else ":root")
    return f"<style>{src}\n:root:root:root {{{tokens}}}</style>"


@lru_cache(maxsize=1)
def _logo_uri() -> str:
    return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")


def esc(v) -> str:
    return _html.escape("" if v is None else str(v))


# ------------------------------------------------------------------ data
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


def sso_email() -> str | None:
    """The signed-in user's email. Databricks Apps signs users in through the workspace (company SSO) and
    forwards their identity in these headers; None when the app is not behind that sign-in."""
    try:
        h = st.context.headers
        for k in ("X-Forwarded-Email", "X-Forwarded-Preferred-Username"):
            if h.get(k):
                return h.get(k)
    except Exception:
        pass
    return None


def user() -> str:
    """Who is making a change: the SSO email on Databricks (app.py refuses to run without it), a local label
    when developing with SQLite."""
    email = sso_email()
    if email:
        return email
    import getpass
    return f"{getpass.getuser()} (local)"


def user_display() -> str:
    u = user()
    name = u.split("@")[0].replace(".", " ").title() if "@" in u else u
    return name


def ledger(as_of: date) -> engine.Ledger:
    d = data()
    return engine.ledger(d["fee_events"], d["contracts"], d["invoices"], d["programs"], as_of)


def require_data() -> bool:
    if data()["contracts"].empty:
        st.info("No data yet. Load your existing folder on the Setup page to get started.")
        st.page_link("views/setup.py", label="Go to Setup")
        return False
    return True


# ------------------------------------------------------------------ formatting
def money(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    if pd.isna(f):
        return ""
    n = round(f)  # whole dollars; also turns -0.0 (e.g. -paid when nothing was paid) into 0
    return f"-${abs(n):,}" if n < 0 else f"${n:,}"


def fmt_date(v) -> str:
    """'1 Oct 2026', as in the demo."""
    return f"{v.day} {v:%b %Y}" if hasattr(v, "strftime") and not pd.isna(v) else ""


def event_label(r) -> str:
    g = r.get if isinstance(r, dict) else (lambda k, d=None: getattr(r, k, d))
    return (f"{g('series')}, FA {int(g('fa_number'))}, due {g('event_date'):%d-%b-%Y}, "
            f"{money(g('expected_amount'))}")


def style_col(df: pd.DataFrame, col: str, mapping: dict, money_cols=(), date_cols=()):
    """Styler for st.dataframe: money and date formats plus chip colours on one column."""
    fmt = {c: (lambda v: money(v) if pd.notna(v) else "") for c in money_cols if c in df.columns}
    fmt.update({c: (lambda v: fmt_date(v) if hasattr(v, "strftime") else ("" if v is None or (isinstance(v, float) and pd.isna(v)) else v))
                for c in date_cols if c in df.columns})
    sty = df.style.format(fmt, na_rep="")
    if col in df.columns:
        sty = sty.map(lambda v: mapping.get(v, ""), subset=[col])
    return sty


def reason_box(key: str, placeholder: str = "Why are you making this change?") -> str:
    return st.text_input("Reason (saved to the audit log)", key=key, placeholder=placeholder)


# ------------------------------------------------------------------ branded HTML
# Each *_html function returns a string so several pieces can be combined into one st.html block (for example a
# Grey band holding a heading, tables and a card); the plain-named functions render directly.

def header(title: str, lede: str | None = None, eyebrow: str | None = None) -> None:
    """Blue Georgia eyebrow above a Dark Blue Georgia title (the title is a real heading for accessibility)."""
    if eyebrow:
        st.markdown(f'<p class="nyl-eyebrow">{esc(eyebrow)}</p>', unsafe_allow_html=True)
    st.title(title)
    if lede:
        st.markdown(f'<p class="nyl-lede">{esc(lede)}</p>', unsafe_allow_html=True)


def chip(text: str, kind: str) -> str:
    if kind not in CHIP_KINDS:
        raise ValueError(f"Unknown chip kind {kind!r}; use one of {sorted(CHIP_KINDS)}")
    return f'<span class="nyl-chip nyl-chip--{kind}">{esc(text)}</span>'


def section_html(eyebrow: str | None, title: str, text: str | None = None, level: int = 2) -> str:
    """Eyebrow, heading and an optional paragraph, for use inside a larger HTML block."""
    return ((f'<p class="nyl-eyebrow">{esc(eyebrow)}</p>' if eyebrow else "")
            + f"<h{level}>{esc(title)}</h{level}>"
            + (f'<p class="nyl-lede">{esc(text)}</p>' if text else ""))


def section(eyebrow: str | None, title: str, text: str | None = None, level: int = 3) -> None:
    st.html(f'<div class="nyl">{section_html(eyebrow, title, text, level)}</div>')


def hero(eyebrow: str, title: str, figure: str, text: str, checks: list[tuple[str, str, str]],
         checks_title: str = "Reconciliation checks") -> None:
    """The signature diagonal: figure on the Dark Blue field, checks on the Grey side.

    checks: (chip text, chip kind, sentence) per row; the chip comes first, as in the demo.
    """
    rows = "".join(f"<li>{chip(ctext, kind)}<span>{esc(sentence)}</span></li>" for ctext, kind, sentence in checks)
    st.html(
        '<section class="nyl-hero"><div class="nyl-hero__field"></div><div class="nyl-hero__inner">'
        f'<div class="nyl-hero__main"><p class="nyl-eyebrow">{esc(eyebrow)}</p>'
        f'<h1 class="nyl-title">{esc(title)}</h1><div class="nyl-figure">{esc(figure)}</div><p>{esc(text)}</p></div>'
        f'<div class="nyl-hero__side"><h3>{esc(checks_title)}</h3><ul class="nyl-checks">{rows}</ul></div>'
        '</div></section>')


def kpi_html(cards: list[tuple[str, str, str]], band: bool = True, columns: int | None = None) -> str:
    cols = columns or min(max(len(cards), 1), 4)
    body = "".join(
        f'<div class="nyl-card"><p class="nyl-kpi__label">{esc(label)}</p><div class="nyl-kpi__value">{esc(value)}</div>'
        + (f'<div class="nyl-kpi__note">{esc(note)}</div>' if note else "") + "</div>"
        for label, value, note in cards)
    grid = f'<div class="nyl-grid nyl-grid--{cols}">{body}</div>'
    return f'<div class="nyl-band">{grid}</div>' if band else grid


def kpi_cards(cards: list[tuple[str, str, str]], band: bool = True) -> None:
    """KPI cards (label, value, note), on a Grey band by default."""
    st.html(kpi_html(cards, band))


DEFAULT_STEPS = [
    ("Upload", "Drop in the Citi PDF. Every fee line is read automatically."),
    ("Match", "Each line is matched one to one with an FA and its anniversary."),
    ("Approve and pay", "One approval and one payment date cover the whole invoice."),
    ("Report", "Month-end accrual, rollforward and Excel package for Finance."),
]


def steps_html(items: list[tuple[str, str]] | None = None) -> str:
    return '<ol class="nyl-steps">' + "".join(
        f'<li class="nyl-step"><span class="nyl-step__title">{esc(t)}</span><span class="nyl-step__text">{esc(x)}</span></li>'
        for t, x in (items or DEFAULT_STEPS)) + "</ol>"


def steps(items: list[tuple[str, str]] | None = None) -> None:
    """The four-step chevron process."""
    st.html(steps_html(items))


WRAP_COLS = ("Explanation", "Description", "Notes", "Change", "Reason")


def table_html(df: pd.DataFrame, money_cols=(), date_cols=(), chips: dict[str, dict] | None = None,
               total_last: bool = False, html_cols=(), num_cols=None) -> str:
    """Small read-only table: Dark Blue header, thin Grey rules, numbers right-aligned.

    chips: column -> {value: chip kind} to show that column's values as chips.
    html_cols: columns whose values are already safe HTML (for example several chips).
    num_cols: columns to right-align (default: money columns and numeric columns).
    total_last: show the last row as a total.
    """
    chips = chips or {}
    num = set(num_cols) if num_cols is not None else {
        c for c in df.columns if c in money_cols or pd.api.types.is_numeric_dtype(df[c])}

    def cell(col, v):
        if col in html_cols:
            return "" if v is None else str(v)
        if col in money_cols:
            return money(v)
        if col in date_cols:
            return esc(fmt_date(v))
        if col in chips and v in chips[col]:
            return chip(str(v), chips[col][v])
        if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)):
            return ""
        return esc(v)

    def cls(c):
        return "num" if c in num else "wrap" if c in WRAP_COLS else ""

    head = "".join(f'<th class="{cls(c)}">{esc(c)}</th>' for c in df.columns)
    body = []
    for i, r in enumerate(df.itertuples(index=False)):
        tr = ' class="is-total"' if total_last and i == len(df) - 1 else ""
        body.append(f"<tr{tr}>" + "".join(f'<td class="{cls(c)}">{cell(c, v)}</td>' for c, v in zip(df.columns, r))
                    + "</tr>")
    return (f'<div class="nyl-table-wrap"><table class="nyl-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def html_table(df: pd.DataFrame, money_cols=(), date_cols=(), chips: dict[str, dict] | None = None,
               total_last: bool = False, html_cols=(), num_cols=None) -> None:
    st.html(table_html(df, money_cols, date_cols, chips, total_last, html_cols, num_cols))


def notice(body_html: str) -> None:
    """A plain notice card (no coloured side bar). body_html must already be escaped."""
    st.html(f'<div class="nyl-notice">{body_html}</div>')


def footer() -> None:
    st.html(f'<footer class="nyl-footer"><img class="nyl-footer__logo" src="{_logo_uri()}" alt="New York Life">'
            '<span>New York Life Insurance Company. For internal use only.</span></footer>')


# ------------------------------------------------------------------ shared wording and data for the pages
def default_as_of() -> date:
    """Pages open on the last completed month-end, the date the accrual is reported at."""
    return last_completed_month_end(date.today())


_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
          "seventeen eighteen nineteen").split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def count_words(n: int) -> str:
    """'Thirteen', 'Forty-two'; digits from 100 up."""
    if n < 20:
        w = _WORDS[n]
    elif n < 100:
        w = _TENS[n // 10 - 2] + ("-" + _WORDS[n % 10] if n % 10 else "")
    else:
        return f"{n:,}"
    return w[0].upper() + w[1:]


def opening_bridge() -> tuple[dict, pd.DataFrame] | None:
    """The tracker-to-tool bridge saved at the initial load (None if the data predates it)."""
    s = data()["settings"]
    row = s.loc[s["key"] == "opening_bridge", "value"]
    if row.empty or not row.iloc[0]:
        return None
    saved = json.loads(row.iloc[0])
    return saved["summary"], pd.DataFrame(saved["rows"])


def bridge_reason(explanation: str, category: str) -> str:
    """Plain-words reason for an opening-balance bridge item, as shown on the month-end page."""
    e = explanation or ""
    if e.startswith("Matures"):
        return "Fee accrued in the maturity year, when none is due"
    if "was paid on invoice" in e:
        return "Fee paid, but still open in another year's tab"
    if "not accrue" in e:
        return "Fee due, but not accrued in the tracker"
    return category


_RECORD = {"contracts": "FA", "invoices": "Invoice", "fee_events": "Fee", "invoice_lines": "Invoice line",
           "snapshots": "Month-end", "programs": "Program", "settings": "Setting", "data_issues": "Exception",
           "documents": "Document", "system": "System", "tracker_row": "Tracker row"}


def audit_record(entity: str, entity_id) -> str:
    return f"{_RECORD.get(entity, entity)} {entity_id}".strip()


def audit_action(action: str) -> str:
    a = (action or "").replace("_", " ")
    return a[:1].upper() + a[1:]


def audit_change(field, old, new) -> str:
    def show(v):
        return "blank" if v is None or v == "" or (isinstance(v, float) and pd.isna(v)) else str(v)
    if field and not (isinstance(field, float) and pd.isna(field)):
        return f"{str(field).replace('_', ' ')}: {show(old)} to {show(new)}"
    return "" if new is None or (isinstance(new, float) and pd.isna(new)) else str(new)
