"""End-to-end check against the real Citi documents (run: python -m tests.test_core <zip>)."""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp()
os.environ["ACCRUALS_BACKEND"] = "sqlite"
os.environ["ACCRUALS_SQLITE_PATH"] = os.path.join(tmp, "t.db")
os.environ["ACCRUALS_LOCAL_FILES"] = os.path.join(tmp, "files")

import pandas as pd  # noqa: E402

from core import engine, reports  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.db import make_backend  # noqa: E402
from core.parsers import parse_term_sheet, unpack_zip, parse_fa_register  # noqa: E402
from core.services import Service  # noqa: E402
from core.storage import FileStore  # noqa: E402

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 20)
pd.set_option("display.max_colwidth", 110)

zip_path = sys.argv[1]
s = load_settings()
db = make_backend(s)
db.ensure_schema()
svc = Service(db, FileStore(s), s)
svc.ensure_defaults("tester")
files = unpack_zip(open(zip_path, "rb").read())
rep = svc.initial_load("tester@nyl.com", files)

print(rep["files"].to_string())
print(rep["invoices"].to_string())
print("\nBRIDGE SUMMARY", rep["bridge_summary"])
print(rep["bridge"].groupby("category")["difference"].agg(["sum", "count"]))
print(rep["bridge"].to_string())

data = svc.load()
as_of = date(2026, 9, 30)
lg = engine.ledger(data["fee_events"], data["contracts"], data["invoices"], data["programs"], as_of)
tot = engine.outstanding_total(lg)
print("\nOutstanding as of 9/30/26:", tot)
print(lg.frame[lg.frame["outstanding"]].groupby("status")["expected_amount"].sum())
roll = engine.rollforward(data["fee_events"], data["contracts"], data["invoices"], data["programs"],
                          date(2026, 8, 31), as_of)
print("Rollforward Sep-26:", roll)
assert roll["check"] == 0

issues = data["data_issues"]
print("\nISSUES by category:\n", issues.groupby("category").size())
print(issues[issues["category"].isin(["Tracker vs register", "Tracker recording error", "Unmatched invoice line",
                                        "Missing source document", "Register data"])][["category", "description"]].to_string())

lines = data["invoice_lines"]
print("\nLine statuses:\n", lines.groupby(["match_status"]).size())
print(lines[lines["match_status"].isin(["yellow", "red"])][["line_id", "series", "match_status", "match_notes"]].to_string())

# Duplicate upload must be blocked
inv_bytes = dict(files)["178352.pdf"]
res = svc.preview_invoice(inv_bytes)
assert res.blocking, "duplicate should block"

# Term sheet
ts = parse_term_sheet(dict(files)["NYLIFE 3-Year FXD Final Issuer Term Sheet - Final.pdf"])
print("\nTERM SHEET", ts)
assert ts.series == "2026-16" and ts.settlement_date == date(2026, 9, 18) and ts.maturity_date == date(2029, 9, 18)
print("Next FA:", svc.next_fa_number(), "Series suggestion:", svc.suggest_series_display("REPACK6883", "2026-16"))

# Register diff against itself -> nothing to do
regname = [n for n, _ in files if n.endswith((".xls", ".xlsx")) and "Funding" in n][0]
reg = parse_fa_register(dict(files)[regname], regname)
diff = svc.register_diff(reg)
print("Register diff new:", len(diff["new"]), "changed:", len(diff["changed"]))

# Excel
fu = reports.followup_frame(lg)
xb = reports.accrual_workbook(as_of, lg, roll, data["invoices"], issues, "tester")
open(os.path.join(tmp, "report.xlsx"), "wb").write(xb)
print("Follow-up rows:", len(fu), "total", fu["Expected fee"].sum())
print(reports.followup_email(fu.head(3), "Tester")[:600])
print("REPORT", os.path.join(tmp, "report.xlsx"))

# Edit + revert + write-off round trip
fa = 70617  # 2019-4, never billed
before = engine.outstanding_total(lg)
ev_id = data["fee_events"].query("fa_number == @fa")["event_id"].iloc[0]
svc.write_off("tester", ev_id, date(2026, 9, 30), "Test write off")
d2 = svc.load()
after = engine.outstanding_total(engine.ledger(d2["fee_events"], d2["contracts"], d2["invoices"], d2["programs"], as_of))
assert after == before - 3000, (before, after)
aid = d2["audit_log"].query("action == 'write_off' and field == 'written_off'")["audit_id"].iloc[0]
print(svc.revert("tester", aid, "undo test"))
svc.update_contract("tester", 70750, {"notes": "checked"}, "note")
print("Audit rows:", len(svc.db.read("audit_log")))

# Reverse an invoice and re-post it -> same result
svc.reverse_invoice("tester", "178352", "test reverse")
res = svc.preview_invoice(inv_bytes)
print("Re-preview counts:", res.counts, [c.status for c in res.checks])
svc.post_invoice("tester", res, file_name="178352.pdf", file_bytes=inv_bytes, approval_date=date(2026, 7, 15),
                 payment_date=date(2026, 7, 15))
d3 = svc.load()
t3 = engine.outstanding_total(engine.ledger(d3["fee_events"], d3["contracts"], d3["invoices"], d3["programs"], as_of))
print("Outstanding after reverse/re-post (2019-4 written off then reinstated):", t3)
print("OK", tmp)
