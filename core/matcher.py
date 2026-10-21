"""One-to-one matching of Citi invoice lines to expected fee events.

GREEN   1:1 match: series, maturity, due date and amount all agree.
YELLOW  1:1 match on series and amount, but a date differs (e.g. Citi's business-day-adjusted
        maturity) or the line was reconstructed without a PDF. Safe to post; worth a glance.
RED     The numbers do not match or there is no clean 1:1 match: unknown series, no fee due on
        that date, amount differs from the expected fee, already billed on another invoice,
        duplicate line, or the invoice total does not tie. Needs a decision before it counts.
PRIOR   Fee date is before the tracking start date (informational, excluded from the accrual).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .config import ACTION_DATE_TOLERANCE_DAYS, MATURITY_TOLERANCE_DAYS
from .parsers import ParsedInvoice

GREEN, YELLOW, RED, PRIOR, MANUAL = "green", "yellow", "red", "prior", "manual"


@dataclass
class LineMatch:
    line_no: int
    series: str
    maturity_date: date
    action_date: date
    amount: float
    currency: str
    status: str
    fa_number: int | None = None
    event_id: str | None = None
    expected_amount: float | None = None
    notes: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)  # event_ids a user could pick instead


@dataclass
class InvoiceCheck:
    name: str
    status: str
    detail: str


@dataclass
class MatchResult:
    invoice: ParsedInvoice
    lines: list[LineMatch]
    checks: list[InvoiceCheck]
    not_billed: pd.DataFrame

    @property
    def counts(self) -> dict[str, int]:
        out = {GREEN: 0, YELLOW: 0, RED: 0, PRIOR: 0, MANUAL: 0}
        for l in self.lines:
            out[l.status] = out.get(l.status, 0) + 1
        return out

    @property
    def blocking(self) -> list[InvoiceCheck]:
        return [c for c in self.checks if c.status == RED and c.name == "Duplicate invoice"]


def match_invoice(inv: ParsedInvoice, contracts: pd.DataFrame, events: pd.DataFrame,
                  invoices: pd.DataFrame, programs: pd.DataFrame, schedule_start: date) -> MatchResult:
    checks: list[InvoiceCheck] = []
    known = set(programs["program_code"])
    checks.append(InvoiceCheck(
        "Program", GREEN if inv.program_code in known else RED,
        f"{inv.program_code} {inv.program_text}".strip() if inv.program_code in known
        else f"Unknown Citi program {inv.program_code}. Add it on the Setup page first.",
    ))
    dup = inv.invoice_no in set(invoices["invoice_no"].astype(str))
    checks.append(InvoiceCheck(
        "Duplicate invoice", RED if dup else GREEN,
        f"Invoice {inv.invoice_no} is already posted." if dup else f"Invoice {inv.invoice_no} is new.",
    ))
    checks.append(InvoiceCheck(
        "Lines tie to total", GREEN if inv.totals_tie else RED,
        f"{len(inv.lines)} lines sum to ${inv.lines_total:,.2f}; invoice total ${inv.fee_total or 0:,.2f}.",
    ))
    for w in inv.warnings:
        checks.append(InvoiceCheck("Parser warning", RED, w))

    prog_contracts = contracts[contracts["program_code"] == inv.program_code]
    prog_events = events[events["program_code"] == inv.program_code]
    already_billed = {
        r.event_id: r.invoice_no for r in prog_events.itertuples() if r.invoice_no and r.invoice_no != inv.invoice_no
    }
    taken: set[str] = set()
    results: list[LineMatch] = []

    for line in inv.lines:
        lm = LineMatch(line.line_no, line.series, line.maturity_date, line.action_date, line.amount,
                       line.currency, RED)
        cands = prog_contracts[prog_contracts["base_series"] == line.series]
        if line.action_date < schedule_start:
            lm.status = PRIOR
            lm.notes.append(f"Fee date {line.action_date:%d-%b-%y} is before tracking start {schedule_start:%d-%b-%y}.")
            lm.fa_number = _closest_anniversary_fa(cands, line.action_date)
            results.append(lm)
            continue
        if cands.empty:
            lm.notes.append(f"No funding agreement with series {line.series} in this program.")
            results.append(lm)
            continue

        options = []  # (date_diff, maturity_diff, event_id, fa, expected)
        cand_fas = set(cands["fa_number"].astype(int))
        ev = prog_events[prog_events["fa_number"].astype(int).isin(cand_fas)]
        mat_by_fa = {int(r.fa_number): r.maturity_date for r in cands.itertuples()}
        for r in ev.itertuples():
            ddiff = abs((r.event_date - line.action_date).days)
            if ddiff > ACTION_DATE_TOLERANCE_DAYS:
                continue
            mdiff = abs((mat_by_fa[int(r.fa_number)] - line.maturity_date).days) if mat_by_fa.get(int(r.fa_number)) else 999
            options.append((ddiff, mdiff, r.event_id, int(r.fa_number), float(r.expected_amount)))
        options.sort()
        lm.candidates = [o[2] for o in options]

        if not options:
            mats = ", ".join(f"FA {int(c.fa_number)} matures {c.maturity_date:%d-%b-%y}" for c in cands.itertuples()
                             if c.maturity_date)
            if any(c.maturity_date and line.action_date >= c.maturity_date for c in cands.itertuples()):
                lm.notes.append(f"Billed on/after maturity, so no fee is due ({mats}).")
            else:
                lm.notes.append(f"No fee is due for series {line.series} near {line.action_date:%d-%b-%y} ({mats}).")
            lm.candidates = list(ev.loc[ev["invoice_no"].isna(), "event_id"])
            results.append(lm)
            continue

        # Choose the best option that is not already used by an earlier line on this invoice.
        chosen = next((o for o in options if o[2] not in taken), None)
        if chosen is None:
            lm.notes.append("Duplicate line: every matching fee is already used by another line on this invoice.")
            results.append(lm)
            continue
        ddiff, mdiff, eid, fa, expected = chosen
        lm.fa_number, lm.event_id, lm.expected_amount = fa, eid, expected
        series_display = cands.loc[cands["fa_number"].astype(int) == fa, "series"].iloc[0]

        if eid in already_billed:
            lm.status = RED
            lm.notes.append(f"Already billed on invoice {already_billed[eid]} (possible double billing).")
        elif abs(line.amount - expected) > 0.005:
            lm.status = RED
            lm.notes.append(f"Amount ${line.amount:,.2f} does not match expected fee ${expected:,.2f}.")
        elif mdiff > MATURITY_TOLERANCE_DAYS:
            lm.status = RED
            lm.notes.append(f"Maturity {line.maturity_date:%d-%b-%y} does not match FA {fa} "
                            f"({mat_by_fa[fa]:%d-%b-%y}).")
        else:
            lm.status = GREEN
            if mdiff:
                lm.status = YELLOW
                lm.notes.append(f"Citi maturity {line.maturity_date:%d-%b-%y} vs register "
                                f"{mat_by_fa[fa]:%d-%b-%y} ({mdiff} day difference, business-day adjustment).")
            if ddiff:
                lm.status = YELLOW
                lm.notes.append(f"Citi fee date {line.action_date:%d-%b-%y} is {ddiff} day(s) from the anniversary.")
            if inv.arrears_start and inv.arrears_end and not (inv.arrears_start <= line.action_date <= inv.arrears_end):
                lm.status = YELLOW
                lm.notes.append("Fee date is outside the invoice's arrears period (late billing).")
        if series_display != line.series:
            lm.notes.append(f"Matched to reopening {series_display} (FA {fa}) by its own anniversary date.")
        elif len(cand_fas) > 1:
            lm.notes.append(f"Series has {len(cand_fas)} FAs; matched FA {fa} by anniversary date.")
        taken.add(eid)
        results.append(lm)

    # Fees due in the arrears period that Citi left off this invoice
    not_billed = pd.DataFrame(columns=["fa_number", "series", "event_date", "expected_amount"])
    if inv.arrears_start and inv.arrears_end:
        e = prog_events.merge(contracts[["fa_number", "series"]], on="fa_number", how="left")
        m = ((e["event_date"] >= inv.arrears_start) & (e["event_date"] <= inv.arrears_end)
             & e["invoice_no"].isna() & ~e["event_id"].isin(taken) & ~e["written_off"])
        not_billed = e.loc[m, ["fa_number", "series", "event_date", "expected_amount"]].sort_values("event_date")
    if len(not_billed):
        checks.append(InvoiceCheck(
            "Fees Citi did not bill", YELLOW,
            f"{len(not_billed)} fee(s) due in the arrears period are not on this invoice "
            f"(${not_billed['expected_amount'].sum():,.0f}). They stay in the accrual and go to Citi follow-up.",
        ))
    return MatchResult(inv, results, checks, not_billed.reset_index(drop=True))


def _closest_anniversary_fa(cands: pd.DataFrame, d: date) -> int | None:
    """Identify which FA a pre-tracking line belongs to (no fee event exists to match against)."""
    from .dates import add_years
    best = None
    for c in cands.itertuples():
        if c.issue_date is None:
            continue
        anniv = add_years(c.issue_date, d.year - c.issue_date.year)
        diff = min(abs((anniv - d).days), abs((add_years(anniv, -1) - d).days), abs((add_years(anniv, 1) - d).days))
        if best is None or diff < best[0]:
            best = (diff, int(c.fa_number))
    return best[1] if best and best[0] <= 7 else None
