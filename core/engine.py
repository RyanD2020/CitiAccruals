"""Business rules for Citi trustee fees.

Confirmed rules
* Citi charges a flat annual fee per funding agreement (FA): $3,000 GMTN, $2,000 EMTN.
* A fee falls due on the issue (settlement) date and on every anniversary of it.
* No fee is due on an anniversary that falls on or after the maturity date.
* No proration: a final period shorter than a year still carries the full fee
  (e.g. 2024-16 was billed $3,000 on 25-Jul-25 with maturity 22-Aug-25).
* Reopenings are separate FAs with their own issue date and their own anniversaries.
* A fee is part of the accrual from its due date until it is paid or written off.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .config import FOLLOW_UP_GRACE_DAYS
from .dates import add_years, quarter_end

STATUS_NOT_DUE = "Not yet due"
STATUS_DUE = "Due - not invoiced"
STATUS_INVOICED = "Invoiced - awaiting approval"
STATUS_APPROVED = "Approved - awaiting payment"
STATUS_PAID = "Paid"
STATUS_WRITTEN_OFF = "Written off"
OPEN_STATUSES = (STATUS_DUE, STATUS_INVOICED, STATUS_APPROVED)


def event_id(fa_number: int, event_date: date) -> str:
    return f"{int(fa_number)}:{event_date.isoformat()}"


def fee_dates(issue_date: date | None, maturity_date: date | None, start: date) -> list[tuple[int, date]]:
    """(period number, due date) for every fee on or after `start`."""
    if issue_date is None or maturity_date is None:
        return []
    out, n = [], 0
    while True:
        due = add_years(issue_date, n)
        if due >= maturity_date:
            break
        if due >= start:
            out.append((n + 1, due))
        n += 1
        if n > 100:
            break
    return out


def contract_fee(contract: dict, programs: pd.DataFrame) -> float:
    override = contract.get("annual_fee_override")
    if override is not None and not pd.isna(override):
        return float(override)
    p = programs.loc[programs["program_code"] == contract["program_code"], "annual_fee"]
    return float(p.iloc[0]) if len(p) else 3000.0


def build_events(contract: dict, programs: pd.DataFrame, start: date, user: str, now) -> list[dict]:
    fee = contract_fee(contract, programs)
    return [
        {
            "event_id": event_id(contract["fa_number"], d),
            "fa_number": int(contract["fa_number"]),
            "program_code": contract["program_code"],
            "event_date": d,
            "period_no": n,
            "expected_amount": fee,
            "invoice_no": None, "line_id": None, "billed_amount": None,
            "written_off": False, "write_off_date": None, "write_off_reason": None, "notes": None,
            "updated_by": user, "updated_at": now,
        }
        for n, d in fee_dates(contract["issue_date"], contract["maturity_date"], start)
    ]


@dataclass
class Ledger:
    """Fee events joined to contract, invoice and program data, with status as of a date."""
    frame: pd.DataFrame
    as_of: date


def ledger(events: pd.DataFrame, contracts: pd.DataFrame, invoices: pd.DataFrame,
           programs: pd.DataFrame, as_of: date) -> Ledger:
    ev = events.copy()
    c = contracts[["fa_number", "series", "base_series", "issue_date", "maturity_date", "reopening_of", "isin"]]
    ev = ev.merge(c, on="fa_number", how="left")
    inv = invoices[["invoice_no", "invoice_date", "approval_date", "payment_date"]].rename(
        columns={"invoice_date": "inv_date"})
    ev = ev.merge(inv, on="invoice_no", how="left")
    prog = programs[["program_code", "short_name", "include_in_accrual"]]
    ev = ev.merge(prog, on="program_code", how="left")
    ev["include_in_accrual"] = ev["include_in_accrual"].fillna(False).astype(bool)

    def status(r) -> str:
        if r["written_off"] and r["write_off_date"] is not None and r["write_off_date"] <= as_of:
            return STATUS_WRITTEN_OFF
        if r["invoice_no"] and _le(r["inv_date"], as_of):
            if _le(r["payment_date"], as_of):
                return STATUS_PAID
            if _le(r["approval_date"], as_of):
                return STATUS_APPROVED
            return STATUS_INVOICED
        if r["event_date"] <= as_of:
            return STATUS_DUE
        return STATUS_NOT_DUE

    ev["status"] = ev.apply(status, axis=1) if len(ev) else pd.Series(dtype=object)
    ev["outstanding"] = ev["status"].isin(OPEN_STATUSES) & ev["include_in_accrual"]
    ev["fee_year"] = ev["event_date"].map(lambda d: d.year)
    ev["quarter_end"] = ev["event_date"].map(quarter_end)
    ev["days_outstanding"] = ev.apply(
        lambda r: (as_of - r["event_date"]).days if r["status"] in OPEN_STATUSES else None, axis=1
    ) if len(ev) else pd.Series(dtype=float)
    ev["follow_up_due"] = ev.apply(
        lambda r: r["status"] == STATUS_DUE and as_of > r["quarter_end"] + timedelta(days=FOLLOW_UP_GRACE_DAYS), axis=1
    ) if len(ev) else pd.Series(dtype=bool)
    return Ledger(ev.sort_values(["event_date", "series"]).reset_index(drop=True), as_of)


def _le(d, as_of: date) -> bool:
    return d is not None and not (isinstance(d, float) and pd.isna(d)) and d <= as_of


def outstanding_total(lg: Ledger) -> float:
    return float(lg.frame.loc[lg.frame["outstanding"], "expected_amount"].sum())


def rollforward(events, contracts, invoices, programs, period_start_exclusive: date, period_end: date) -> dict:
    """Opening + new fees - paid - written off = closing (with a check line)."""
    open_lg = ledger(events, contracts, invoices, programs, period_start_exclusive)
    close_lg = ledger(events, contracts, invoices, programs, period_end)
    o, c = open_lg.frame, close_lg.frame
    inc = c["include_in_accrual"]
    in_period = (c["event_date"] > period_start_exclusive) & (c["event_date"] <= period_end)
    added = float(c.loc[inc & in_period, "expected_amount"].sum())
    was_open = o.set_index("event_id")["outstanding"]
    open_flag = c["event_id"].map(was_open).fillna(False).astype(bool) | (inc & in_period)
    paid = float(c.loc[open_flag & (c["status"] == STATUS_PAID), "expected_amount"].sum())
    wo = float(c.loc[open_flag & (c["status"] == STATUS_WRITTEN_OFF), "expected_amount"].sum())
    opening, closing = outstanding_total(open_lg), outstanding_total(close_lg)
    return {
        "opening": opening, "new_fees": added, "paid": paid, "written_off": wo, "closing": closing,
        "check": round(opening + added - paid - wo - closing, 2),
    }


def expected_not_billed(lg: Ledger, program_code: str, period_start: date, period_end: date,
                        billed_event_ids: set[str]) -> pd.DataFrame:
    """Fees due inside an invoice's arrears period that Citi did not bill on it (or earlier)."""
    f = lg.frame
    mask = (
        (f["program_code"] == program_code)
        & (f["event_date"] >= period_start) & (f["event_date"] <= period_end)
        & f["invoice_no"].isna() & ~f["event_id"].isin(billed_event_ids) & ~f["written_off"]
    )
    return f.loc[mask, ["fa_number", "series", "event_date", "expected_amount"]]
