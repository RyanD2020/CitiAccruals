"""End-to-end check against the real Citi documents.

    python -m tests.test_core <zip>     prints the full detail and checks the regression baseline

The release pipeline (notebooks/pipeline/02_test.py) calls run(zip, verbose=False) and check_baseline(),
so only aggregate counts and totals are ever printed there, never document content.
"""
import os
import shutil
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from core import engine, reports  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.db import make_backend  # noqa: E402
from core.parsers import parse_term_sheet, unpack_zip, parse_fa_register  # noqa: E402
from core.services import Service  # noqa: E402
from core.storage import FileStore  # noqa: E402

AS_OF = date(2026, 9, 30)

# Regression baseline confirmed by the business. Change only with their sign-off.
BASELINE = {
    "pdf_invoices_posted": 12,
    "lines_green": 145,
    "lines_yellow": 36,
    "lines_red": 0,
    "lines_prior": 6,
    "prior_invoices": ["127932"],
    "outstanding_2026_09_30": 177000.0,
    "bridge_items": 29,
    "bridge_total": -27000.0,
    "rollforward_check_2026_09": 0.0,
    "next_fa_number": 70751,
}


def run(zip_path: str, verbose: bool = True) -> dict:
    """Load the documents into a scratch SQLite database, exercise every workflow, return the metrics."""
    log = print if verbose else (lambda *a, **k: None)
    tmp = tempfile.mkdtemp()
    os.environ["ACCRUALS_BACKEND"] = "sqlite"
    os.environ["ACCRUALS_SQLITE_PATH"] = os.path.join(tmp, "t.db")
    os.environ["ACCRUALS_LOCAL_FILES"] = os.path.join(tmp, "files")
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.max_colwidth", 110)
    try:
        return _run(zip_path, tmp, log)
    finally:
        if not verbose:
            shutil.rmtree(tmp, ignore_errors=True)


def _run(zip_path: str, tmp: str, log) -> dict:
    s = load_settings()
    db = make_backend(s)
    db.ensure_schema()
    svc = Service(db, FileStore(s), s)
    svc.ensure_defaults("tester")
    with open(zip_path, "rb") as fh:
        files = unpack_zip(fh.read())
    rep = svc.initial_load("tester@nyl.com", files)

    log(rep["files"].to_string())
    log(rep["invoices"].to_string())
    log("\nBRIDGE SUMMARY", rep["bridge_summary"])
    log(rep["bridge"].groupby("category")["difference"].agg(["sum", "count"]))
    log(rep["bridge"].to_string())

    data = svc.load()
    lg = engine.ledger(data["fee_events"], data["contracts"], data["invoices"], data["programs"], AS_OF)
    tot = engine.outstanding_total(lg)
    log("\nOutstanding as of 9/30/26:", tot)
    log(lg.frame[lg.frame["outstanding"]].groupby("status")["expected_amount"].sum())
    roll = engine.rollforward(data["fee_events"], data["contracts"], data["invoices"], data["programs"],
                              date(2026, 8, 31), AS_OF)
    log("Rollforward Sep-26:", roll)
    assert roll["check"] == 0

    issues = data["data_issues"]
    log("\nISSUES by category:\n", issues.groupby("category").size())
    log(issues[issues["category"].isin(["Tracker vs register", "Tracker recording error", "Unmatched invoice line",
                                        "Missing source document", "Register data"])][["category", "description"]].to_string())

    lines = data["invoice_lines"]
    log("\nLine statuses:\n", lines.groupby(["match_status"]).size())
    log(lines[lines["match_status"].isin(["yellow", "red"])][["line_id", "series", "match_status", "match_notes"]].to_string())

    status_counts = lines["match_status"].value_counts()
    metrics = {
        "pdf_invoices_posted": int((data["invoices"]["source"] == "pdf").sum()),
        "lines_green": int(status_counts.get("green", 0)),
        "lines_yellow": int(status_counts.get("yellow", 0)),
        "lines_red": int(status_counts.get("red", 0)),
        "lines_manual": int(status_counts.get("manual", 0)),
        "lines_prior": int(status_counts.get("prior", 0)),
        "prior_invoices": sorted(lines.loc[lines["match_status"] == "prior", "invoice_no"].unique().tolist()),
        "outstanding_2026_09_30": tot,
        "tracker_total": rep["bridge_summary"]["tracker_total"],
        "bridge_items": int(len(rep["bridge"])),
        "bridge_total": float(rep["bridge"]["difference"].sum()) if len(rep["bridge"]) else 0.0,
        "rollforward_check_2026_09": roll["check"],
    }

    # Corrected tracker workbook: with every change applied it must equal the tool's accrual, and a "keep current"
    # decision must move the total by exactly that row's effect.
    from core import workbook
    _, trk = svc.tracker_document()
    found = workbook.findings(trk, data, AS_OF)
    _, tot = workbook.corrected_workbook(trk, data, AS_OF, {})
    assert tot["corrected_total"] == tot["ledger_total"], tot
    big = max(found, key=lambda f: abs(f.effect))
    svc.decide_tracker_rows("tester", {big.id: ("keep", "test")}, "test")
    _, tot_keep = workbook.corrected_workbook(trk, svc.load(), AS_OF, svc.tracker_decisions())
    assert abs(tot_keep["corrected_total"] - (tot["corrected_total"] - big.effect)) < 0.005, (tot_keep, big.effect)
    assert len(svc.db.read("audit_log").query("action == 'decide'")) == 1
    svc.decide_tracker_rows("tester", {big.id: (None, None)}, "test")
    # From 2027 the workbook keeps three year tabs: a 2027 tab is built, 2024 moves to the archive, totals still tie.
    _, tot27 = workbook.corrected_workbook(trk, data, date(2027, 1, 31), {}, view="current")
    assert tot27["new_years"] == [2027] and tot27["archived"] == [2024], tot27
    assert tot27["corrected_total"] == tot27["ledger_total"], tot27
    metrics.update(tracker_rows_total=workbook.tracker_total(trk), tracker_findings=len(found),
                   corrected_workbook_total=tot["corrected_total"])
    log("Tracker clean-up:", len(found), "rows differ; corrected workbook total", tot["corrected_total"])

    # Duplicate upload must be blocked
    inv_bytes = dict(files)["178352.pdf"]
    res = svc.preview_invoice(inv_bytes)
    assert res.blocking, "duplicate should block"
    metrics["duplicate_upload_blocked"] = True

    # Term sheet
    ts = parse_term_sheet(dict(files)["NYLIFE 3-Year FXD Final Issuer Term Sheet - Final.pdf"])
    log("\nTERM SHEET", ts)
    assert ts.series == "2026-16" and ts.settlement_date == date(2026, 9, 18) and ts.maturity_date == date(2029, 9, 18)
    metrics["next_fa_number"] = svc.next_fa_number()
    log("Next FA:", metrics["next_fa_number"], "Series suggestion:", svc.suggest_series_display("REPACK6883", "2026-16"))

    # Register diff against itself -> nothing to do
    regname = [n for n, _ in files if n.endswith((".xls", ".xlsx")) and "Funding" in n][0]
    reg = parse_fa_register(dict(files)[regname], regname)
    diff = svc.register_diff(reg)
    log("Register diff new:", len(diff["new"]), "changed:", len(diff["changed"]))
    metrics["register_diff_new"], metrics["register_diff_changed"] = len(diff["new"]), len(diff["changed"])

    # Excel
    fu = reports.followup_frame(lg)
    xb = reports.accrual_workbook(AS_OF, lg, roll, data["invoices"], issues, "tester")
    with open(os.path.join(tmp, "report.xlsx"), "wb") as fh:
        fh.write(xb)
    log("Follow-up rows:", len(fu), "total", fu["Expected fee"].sum())
    log(reports.followup_email(fu.head(3), "Tester")[:600])
    log("REPORT", os.path.join(tmp, "report.xlsx"))
    metrics["followup_rows"] = int(len(fu))

    # Edit + revert + write-off round trip
    fa = 70617  # 2019-4, never billed
    before = engine.outstanding_total(lg)
    ev_id = data["fee_events"].query("fa_number == @fa")["event_id"].iloc[0]
    svc.write_off("tester", ev_id, date(2026, 9, 30), "Test write off")
    d2 = svc.load()
    after = engine.outstanding_total(engine.ledger(d2["fee_events"], d2["contracts"], d2["invoices"], d2["programs"], AS_OF))
    assert after == before - 3000, (before, after)
    aid = d2["audit_log"].query("action == 'write_off' and field == 'written_off'")["audit_id"].iloc[0]
    log(svc.revert("tester", aid, "undo test"))
    svc.update_contract("tester", 70750, {"notes": "checked"}, "note")
    log("Audit rows:", len(svc.db.read("audit_log")))

    # Reverse an invoice and re-post it -> same result
    svc.reverse_invoice("tester", "178352", "test reverse")
    res = svc.preview_invoice(inv_bytes)
    log("Re-preview counts:", res.counts, [c.status for c in res.checks])
    svc.post_invoice("tester", res, file_name="178352.pdf", file_bytes=inv_bytes, approval_date=date(2026, 7, 15),
                     payment_date=date(2026, 7, 15))
    d3 = svc.load()
    t3 = engine.outstanding_total(engine.ledger(d3["fee_events"], d3["contracts"], d3["invoices"], d3["programs"], AS_OF))
    log("Outstanding after reverse/re-post (2019-4 written off then reinstated):", t3)
    metrics["outstanding_after_repost"] = t3
    log("OK", tmp)
    return metrics


def check_baseline(metrics: dict) -> list[str]:
    """Every difference from BASELINE, as 'name: expected X, got Y'. Empty means the baseline holds."""
    problems = []
    for key, want in BASELINE.items():
        got = metrics.get(key)
        same = (got is not None and abs(float(got) - float(want)) < 0.005) if isinstance(want, float) else got == want
        if not same:
            problems.append(f"{key}: expected {want!r}, got {got!r}")
    return problems


if __name__ == "__main__":
    m = run(sys.argv[1])
    problems = check_baseline(m)
    print("\nBASELINE", "OK" if not problems else "FAILED")
    for p in problems:
        print("  " + p)
    sys.exit(1 if problems else 0)
