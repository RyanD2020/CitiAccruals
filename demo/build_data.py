"""Build the demo's data from the app's real engine.

    python -m demo.build_data          (documents zip from ACCRUALS_DEMO_ZIP, default Citi_Accruals_Documents.zip)

Loads the documents into a scratch SQLite database with Service.initial_load, exports every figure as of
30 September 2026 to demo/out/demo_data.json (or $ACCRUALS_DEMO_OUT_DIR), and fails unless the figures
match the confirmed values. demo/out/ holds real figures and is git-ignored.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

AS_OF = date(2026, 9, 30)
PREV_MONTH_END = date(2026, 8, 31)
NEXT_Q = (date(2026, 10, 1), date(2026, 12, 31))
USER = "analyst@newyorklife.com"
OUT_DIR = os.environ.get("ACCRUALS_DEMO_OUT_DIR", os.path.join(ROOT, "demo", "out"))
OUT = os.path.join(OUT_DIR, "demo_data.json")

# Confirmed figures. The build fails if any differ.
EXPECTED = {
    "outstanding": 177000.0,
    "fee_count": 59,
    "roll_opening": 144000.0,
    "roll_new_fees": 33000.0,
    "roll_closing": 177000.0,
    "roll_check": 0.0,
    "bridge_items": 29,
    "bridge_total": -27000.0,
    "tracker_total": 204000.0,
    "invoices": 13,
    "followup_rows": 61,
    "followup_past_window_rows": 34,
    "followup_past_window_total": 102000.0,
    "never_billed_fas": 18,
    "problem_green": 24,
    "problem_yellow": 1,
    "problem_red": 3,
    "next_quarter_fees": 9,
    "next_quarter_total": 27000.0,
    "active_agreements": 85,
}

BANK_PATTERNS = [r"\bIBAN\b", r"\bSWIFT\b", r"Account\s*No", r"CITIUS33", r"\bABA\b", r"(?i)routing number",
                 r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"]


def j(v):
    """Plain JSON value: ISO dates, None for NaN/NaT/NA, Python numbers."""
    import pandas as pd
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        v = v.to_pydatetime()
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v.item() if hasattr(v, "item") else v


def records(df, cols: dict[str, str]) -> list[dict]:
    return [{new: j(r[old]) for old, new in cols.items()} for r in df.to_dict("records")]


def event_label(series, fa, due, amount) -> str:
    return f"{series}, FA {int(fa)}, due {due:%d-%b-%Y}, ${amount:,.0f}"


def collect(zip_path: str) -> tuple[dict, dict, set[str]]:
    """Run the engine and return (demo data, measured figures, approver names seen in the documents)."""
    tmp = tempfile.mkdtemp()
    os.environ["ACCRUALS_BACKEND"] = "sqlite"
    os.environ["ACCRUALS_SQLITE_PATH"] = os.path.join(tmp, "demo.db")
    os.environ["ACCRUALS_LOCAL_FILES"] = os.path.join(tmp, "files")
    try:
        return _collect(zip_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _collect(zip_path: str):
    from core import engine, reports
    from core.config import CITI_BILLING_EMAIL, INVOICE_EXPECTED_DAYS_AFTER_QUARTER, load_settings
    from core.dates import quarter_bounds, quarter_label, quarter_of
    from core.db import make_backend
    from core.matcher import GREEN, PRIOR, RED, YELLOW
    from core.parsers import InvoiceLine, parse_invoice, unpack_zip
    from core.services import Service
    from core.storage import FileStore

    s = load_settings()
    db = make_backend(s)
    db.ensure_schema()
    svc = Service(db, FileStore(s), s)
    svc.ensure_defaults(USER)
    with open(zip_path, "rb") as fh:
        files = unpack_zip(fh.read())
    rep = svc.initial_load(USER, files, as_of=AS_OF)
    data = svc.load()
    approvers = {str(n).strip() for n in data["invoices"]["approved_by"].dropna() if str(n).strip()}

    lg = engine.ledger(data["fee_events"], data["contracts"], data["invoices"], data["programs"], AS_OF)
    f = lg.frame
    out = f[f["outstanding"]]
    due = out[out["status"] == engine.STATUS_DUE]
    billed = out[out["status"].isin([engine.STATUS_INVOICED, engine.STATUS_APPROVED])]
    roll = engine.rollforward(data["fee_events"], data["contracts"], data["invoices"], data["programs"],
                              PREV_MONTH_END, AS_OF)

    by_year = out.groupby("fee_year")["expected_amount"].agg(["count", "sum"]).reset_index()
    by_status = out.groupby("status")["expected_amount"].agg(["count", "sum"]).reset_index()

    inc = f[f["include_in_accrual"]]
    nq = inc[(inc["event_date"] >= NEXT_Q[0]) & (inc["event_date"] <= NEXT_Q[1]) & ~inc["written_off"]]
    nq = nq.sort_values(["event_date", "series"])

    # Invoices with their lines
    progs = data["programs"].set_index("program_code")["short_name"]
    lines = data["invoice_lines"]
    invoices = []
    for inv in data["invoices"].sort_values(["invoice_date", "invoice_no"]).to_dict("records"):
        il = lines[lines["invoice_no"] == inv["invoice_no"]].sort_values("line_no")
        counts = il["match_status"].value_counts()
        invoices.append({
            "invoice_no": inv["invoice_no"], "program": progs.get(inv["program_code"], inv["program_code"]),
            "invoice_date": j(inv["invoice_date"]), "arrears_start": j(inv["arrears_start"]),
            "arrears_end": j(inv["arrears_end"]), "total": j(inv["fee_total"]), "line_count": j(inv["line_count"]),
            "approval_date": j(inv["approval_date"]), "payment_date": j(inv["payment_date"]),
            "source": inv["source"], "counts": {k: int(v) for k, v in counts.items()},
            "lines": records(il, {"line_no": "line_no", "series": "series", "maturity_date": "maturity_date",
                                  "action_date": "fee_date", "amount": "amount", "fa_number": "fa_number",
                                  "match_status": "status", "match_notes": "notes"}),
        })

    # Active agreements and their fee schedules
    c = data["contracts"]
    active = c[c["maturity_date"].map(lambda d: d is not None and d > AS_OF)].sort_values(["program_code", "fa_number"])
    agreements = []
    for r in active.to_dict("records"):
        ev = f[f["fa_number"] == r["fa_number"]].sort_values("event_date")
        agreements.append({
            "fa_number": int(r["fa_number"]), "series": r["series"], "program": progs.get(r["program_code"]),
            "isin": r["isin"], "issue_date": j(r["issue_date"]), "maturity_date": j(r["maturity_date"]),
            "currency": r["currency"], "reopening_of": j(r["reopening_of"]),
            "outstanding": float(ev.loc[ev["outstanding"], "expected_amount"].sum()),
            "schedule": records(ev, {"event_date": "due", "period_no": "period", "expected_amount": "amount",
                                     "status": "status", "invoice_no": "invoice_no"}),
        })

    # Citi follow-up
    fu = reports.followup_frame(lg)
    fu["past_window"] = ~fu["Why"].str.endswith("(invoice not yet due)")
    fu["never_billed"] = fu["Why"].str.startswith("Citi has never billed")
    followup = records(fu, {"Program": "program", "Series": "series", "FA #": "fa_number", "ISIN": "isin",
                            "Issue date": "issue_date", "Maturity date": "maturity_date", "Fee due date": "due",
                            "Expected fee": "amount", "Days since due": "days", "Why": "why",
                            "past_window": "past_window", "never_billed": "never_billed"})

    bridge = rep["bridge"].sort_values(["category", "fee_year", "series"])
    bsum = rep["bridge_summary"]
    bridge_groups = bridge.groupby("category")["difference"].agg(["count", "sum"]).reset_index()
    issues = data["data_issues"]
    other_issues = issues[issues["category"] != "Opening balance reconciliation"].sort_values(["category", "entity_id"])

    # Reconciliation checks for the hero
    y, q = quarter_of(AS_OF)
    last_q = (y - 1, 4) if q == 1 else (y, q - 1)
    qs, qe = quarter_bounds(*last_q)
    got = data["invoices"][(data["invoices"]["program_code"] == "REPACK6883") & (data["invoices"]["arrears_end"] == qe)]
    cur_qe = quarter_bounds(y, q)[1]
    expected_by = cur_qe + timedelta(days=INVOICE_EXPECTED_DAYS_AFTER_QUARTER)
    # Counted over fees past their invoice window, as the Citi follow-up page shows by default.
    never_fas = int(fu.loc[fu["never_billed"] & fu["past_window"], "FA #"].nunique())
    unexplained = round(bsum["difference"] - bsum["explained"], 2)
    checks = [
        {"label": "September rollforward ties", "value": f"difference ${roll['check']:,.0f}",
         "chip": "pass" if roll["check"] == 0 else "fail", "chip_text": "Pass" if roll["check"] == 0 else "Fail"},
        {"label": "Tracker difference explained", "value": f"{len(bridge)} items, ${unexplained:,.0f} unexplained",
         "chip": "pass" if unexplained == 0 else "fail", "chip_text": "Pass" if unexplained == 0 else "Fail"},
        {"label": f"{quarter_label(qe)} GMTN invoice", "value": f"received, {got['invoice_no'].iloc[0]}" if len(got) else "not received",
         "chip": "pass" if len(got) else "fail", "chip_text": "Pass" if len(got) else "Follow up"},
        {"label": f"{quarter_label(cur_qe)} GMTN invoice", "value": f"due by about {expected_by:%d %B %Y}",
         "chip": "info", "chip_text": "Expected"},
        {"label": "FAs Citi has never billed", "value": f"{never_fas} agreements",
         "chip": "review" if never_fas else "pass", "chip_text": "Follow up" if never_fas else "Pass"},
    ]

    demo = {
        "as_of": AS_OF.isoformat(),
        "prev_month_end": PREV_MONTH_END.isoformat(),
        "citi_email": CITI_BILLING_EMAIL,
        "kpis": {
            "outstanding": float(out["expected_amount"].sum()), "fee_count": int(len(out)),
            "due_not_invoiced": float(due["expected_amount"].sum()), "due_not_invoiced_count": int(len(due)),
            "invoiced_awaiting_payment": float(billed["expected_amount"].sum()),
            "invoiced_awaiting_payment_count": int(len(billed)),
        },
        "by_fee_year": [{"year": int(r["fee_year"]), "count": int(r["count"]), "amount": float(r["sum"])}
                        for r in by_year.to_dict("records")],
        "by_status": [{"status": r["status"], "count": int(r["count"]), "amount": float(r["sum"])}
                      for r in by_status.to_dict("records")],
        "rollforward": {k: float(v) for k, v in roll.items()},
        "next_quarter": records(nq, {"event_date": "due", "series": "series", "fa_number": "fa_number",
                                     "short_name": "program", "expected_amount": "amount"}),
        "invoices": invoices,
        "agreements": agreements,
        "followup": followup,
        "bridge": records(bridge, {"fa_number": "fa_number", "series": "series", "fee_year": "fee_year",
                                   "tracker_owed": "tracker_owed", "tool_owed": "tool_owed", "difference": "difference",
                                   "category": "category", "explanation": "explanation"}),
        "bridge_groups": [{"category": r["category"], "count": int(r["count"]), "amount": float(r["sum"])}
                          for r in bridge_groups.to_dict("records")],
        "bridge_summary": {k: j(v) for k, v in bsum.items()},
        "issues": records(other_issues, {"category": "category", "entity_id": "reference",
                                         "description": "description", "amount_impact": "amount", "status": "status"}),
        "checks": checks,
        "audit": [  # illustrative, not real history
            {"ts": "2026-10-01 09:12", "user": "analyst@newyorklife.com", "action": "Month-end finalized",
             "entity": "Snapshot 2026-09-30", "detail": "Accrual $177,000 frozen with the Excel package"},
            {"ts": "2026-09-30 16:40", "user": "analyst@newyorklife.com", "action": "Note added",
             "entity": "FA 70750", "detail": "notes: (blank) to \"Term sheet checked\""},
            {"ts": "2026-09-22 11:05", "user": "reviewer@newyorklife.com", "action": "Payment recorded",
             "entity": "Invoice 178352", "detail": "payment_date: (blank) to 2026-07-15"},
            {"ts": "2026-09-18 14:31", "user": "analyst@newyorklife.com", "action": "Agreement added",
             "entity": "FA 70750", "detail": "Series 2026-16 from the term sheet; 3 fees scheduled"},
            {"ts": "2026-08-04 10:02", "user": "reviewer@newyorklife.com", "action": "Write-off reverted",
             "entity": "FA 70617", "detail": "written_off: true to false (reason: confirmed still billable)"},
            {"ts": "2026-07-16 08:47", "user": "analyst@newyorklife.com", "action": "Invoice posted",
             "entity": "Invoice 178352", "detail": "25 lines: 25 match, 0 review, 0 no match"},
        ],
    }

    # ---- upload samples (after every export above: reversing 178352 changes the scratch database) ----
    raw = dict(files)["178352.pdf"]
    svc.reverse_invoice(USER, "178352", "Demo: show 178352 as a new upload")
    real = svc.preview_invoice(raw)

    prob_inv = copy.deepcopy(parse_invoice(raw))
    prob_inv.invoice_no = "EXAMPLE"
    prob_inv.approved_by = None
    by_no = {l.line_no: l for l in prob_inv.lines}
    by_no[5].amount = 1500.0
    by_no[10].maturity_date = date(2034, 5, 4)
    n = max(by_no)
    prob_inv.lines += [
        replace(by_no[1], line_no=n + 1),
        InvoiceLine(n + 2, "2027-1", date(2030, 4, 1), date(2026, 4, 1), 3000.0, "USD"),
        InvoiceLine(n + 3, "2019-4", date(2027, 10, 18), date(2026, 4, 18), 3000.0, "USD"),
    ]
    prob_inv.fee_total = round(sum(l.amount for l in prob_inv.lines), 2)
    problem = svc.match(prob_inv)

    d2 = svc.load()
    ev2 = d2["fee_events"].merge(d2["contracts"][["fa_number", "series"]], on="fa_number", how="left").set_index("event_id")
    contracts2 = d2["contracts"].set_index("fa_number")

    def sample(result, label, note):
        inv = result.invoice
        out_lines = []
        for lm in result.lines:
            e = ev2.loc[lm.event_id] if lm.event_id in ev2.index else None
            options = []
            if lm.status == RED:
                for eid in dict.fromkeys(([lm.event_id] if lm.event_id else []) + lm.candidates):
                    if eid in ev2.index:
                        x = ev2.loc[eid]
                        options.append({"event_id": eid, "label": event_label(x["series"], x["fa_number"],
                                                                              x["event_date"], x["expected_amount"])})
            out_lines.append({
                "line_no": lm.line_no, "status": lm.status, "series": lm.series,
                "maturity_date": j(lm.maturity_date), "fee_date": j(lm.action_date), "amount": lm.amount,
                "fa_number": lm.fa_number,
                "our_series": contracts2.loc[lm.fa_number, "series"] if lm.fa_number in contracts2.index else None,
                "anniversary": j(e["event_date"]) if e is not None else None,
                "expected": lm.expected_amount, "notes": lm.notes, "options": options,
            })
        nb = result.not_billed
        return {
            "key": label, "note": note, "file_name": "178352.pdf" if label == "real" else "178352 (edited copy).pdf",
            "invoice_no": inv.invoice_no, "program": inv.program_code, "invoice_date": j(inv.invoice_date),
            "arrears_start": j(inv.arrears_start), "arrears_end": j(inv.arrears_end), "total": inv.fee_total,
            "approval_date": j(inv.approval_date),
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in result.checks],
            "counts": {GREEN: result.counts[GREEN], YELLOW: result.counts[YELLOW], RED: result.counts[RED],
                       PRIOR: result.counts[PRIOR]},
            "lines": out_lines,
            "not_billed": records(nb, {"fa_number": "fa_number", "series": "series", "event_date": "due",
                                       "expected_amount": "amount"}),
        }

    demo["samples"] = {
        "real": sample(real, "real", "Citi invoice 178352 as received"),
        "problem": sample(problem, "problem", "Edited copy of 178352: one amount and one maturity changed, "
                                              "and three lines added"),
    }

    fu_past = fu[fu["past_window"]]
    measured = {
        "outstanding": demo["kpis"]["outstanding"], "fee_count": demo["kpis"]["fee_count"],
        "roll_opening": roll["opening"], "roll_new_fees": roll["new_fees"], "roll_closing": roll["closing"],
        "roll_check": roll["check"],
        "bridge_items": len(bridge), "bridge_total": float(bridge["difference"].sum()),
        "tracker_total": bsum["tracker_total"],
        "invoices": len(invoices),
        "followup_rows": len(fu), "followup_past_window_rows": len(fu_past),
        "followup_past_window_total": float(fu_past["Expected fee"].sum()), "never_billed_fas": never_fas,
        "problem_green": problem.counts[GREEN], "problem_yellow": problem.counts[YELLOW],
        "problem_red": problem.counts[RED],
        "next_quarter_fees": len(nq), "next_quarter_total": float(nq["expected_amount"].sum()),
        "active_agreements": len(agreements),
    }
    return demo, measured, approvers


def check_figures(measured: dict) -> list[str]:
    problems = []
    for k, want in EXPECTED.items():
        got = measured.get(k)
        ok = got is not None and (abs(float(got) - float(want)) < 0.005 if isinstance(want, float) else got == want)
        if not ok:
            problems.append(f"{k}: expected {want!r}, got {got!r}")
    return problems


def scan_text(text: str, names: set[str]) -> list[str]:
    """Person names seen in the documents, or anything that looks like bank details."""
    found = [f"bank detail pattern {p!r}" for p in BANK_PATTERNS if re.search(p, text)]
    found += ["an approver name from the invoices" for n in names if len(n) > 2 and n.lower() in text.lower()]
    if re.search(r"(?i)ok(?:ay)?\s+to\s+pay\s*/", text):
        found.append("an 'okay to pay' stamp")
    return found


def main() -> int:
    zip_path = os.environ.get("ACCRUALS_DEMO_ZIP", "Citi_Accruals_Documents.zip")
    demo, measured, names = collect(zip_path)
    problems = check_figures(measured)
    text = json.dumps(demo, ensure_ascii=False, indent=1)
    problems += [f"demo data contains {x}" for x in scan_text(text, names)]
    if problems:
        print("Demo data NOT written. Differences:")
        for p in problems:
            print("  " + p)
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"Wrote {OUT}: all {len(EXPECTED)} figures match, no names or bank details.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
