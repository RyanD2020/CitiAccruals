from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

import pandas as pd


def add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + n, day=28)


def quarter_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def quarter_bounds(year: int, q: int) -> tuple[date, date]:
    start = date(year, 3 * (q - 1) + 1, 1)
    end_month = 3 * q
    end = date(year, end_month, calendar.monthrange(year, end_month)[1])
    return start, end


def quarter_end(d: date) -> date:
    return quarter_bounds(*quarter_of(d))[1]


def quarter_label(d: date) -> str:
    y, q = quarter_of(d)
    return f"Q{q} {y}"


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def previous_month_end(d: date) -> date:
    return d.replace(day=1) - timedelta(days=1)


def last_completed_month_end(today: date) -> date:
    me = month_end(today)
    return me if today == me else previous_month_end(today)


_FORMATS = ["%d-%b-%y", "%d-%b-%Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%d %B %Y",
            "%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]


def parse_date(value) -> date | None:
    """Parse the date formats found in Citi invoices, term sheets and the FA register."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    if not s:
        return None
    m = re.match(r"(\d{1,2}/\d{1,2}/\d{4})", s)          # "5/7/2010 (final maturity)"
    if m:
        s = m.group(1)
    m = re.match(r"([A-Za-z]+\.? \d{1,2}, \d{4})", s)      # "September 18, 2026 (T+3)(3)"
    if m:
        s = m.group(1).replace(".", "")
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fmt_date(d) -> str:
    d = parse_date(d)
    return d.strftime("%d-%b-%Y") if d else ""
