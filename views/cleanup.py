"""Tracker clean-up: every row of the old tracker that disagrees with the invoices and the fee schedule, a decision
on each, and the corrected tracker workbook to compare side by side with the manual one."""
import hashlib

import pandas as pd
import streamlit as st

from core import workbook
from views import ui

ui.header("Fix the old tracker, row by row",
          "Every row of the old accrual tracker that disagrees with the Citi invoices and the fee schedule. Review each "
          "change, then apply it or keep the tracker's value. The corrected workbook is a copy of the tracker with only "
          "these cells changed and highlighted, so it can be compared side by side with the manual one.",
          eyebrow="Tracker clean-up")
if not ui.require_data():
    st.stop()

svc = ui.service()
d = ui.data()
doc = svc.tracker_document()
if doc is None:
    st.info("No tracker workbook on file. It is saved when the folder is loaded on Setup.")
    st.stop()
name, raw = doc
as_of = st.columns([1, 4])[0].date_input("As of", value=ui.default_as_of(), format="DD/MM/YYYY")


@st.cache_data(show_spinner="Comparing the tracker with the fee ledger...")
def _findings(file_hash: str, as_of, version: int) -> list[workbook.Finding]:
    return workbook.findings(raw, ui.data(), as_of)


found = _findings(hashlib.sha256(raw).hexdigest(), as_of, len(d["audit_log"]))
decisions = svc.tracker_decisions(d)
if not found:
    st.success("The tracker agrees with the fee ledger on every row. Nothing to fix.")
    st.stop()

tracker_owed = workbook.tracker_total(raw)
kept = [f for f in found if decisions.get(f.id, {}).get("decision") == "keep"]
decided = [f for f in found if f.id in decisions]
review = [f for f in found if decisions.get(f.id, {}).get("decision") == "review"]
corrected_owed = tracker_owed + sum(f.effect for f in found if f not in kept)
tool_owed = tracker_owed + sum(f.effect for f in found)
ui.kpi_cards([
    ("Old tracker owes", ui.money(tracker_owed), name),
    ("Corrected workbook owes", ui.money(corrected_owed), f"{len(found) - len(kept)} of {len(found)} changes applied"),
    ("Tool accrual", ui.money(tool_owed), f"at {ui.fmt_date(as_of)}"),
    ("Rows decided", f"{len(decided)} of {len(found)}", f"{len(review)} need more review" if review else "none waiting"),
])
if kept:
    st.caption(f"Rows kept at the tracker's value leave {ui.money(corrected_owed - tool_owed)} between the corrected "
               "workbook and the tool.")

LABEL = {None: "Not decided", "apply": "Apply", "keep": "Keep current", "review": "Needs more review"}
CODE = {v: k for k, v in LABEL.items()}
ORDER = ["paid_not_recorded", "wrong_tab", "not_billed", "no_fee_due", "not_accrued", "not_yet_due", "missing_row",
         "other"]
pending: dict[str, tuple] = {}

for kind in ORDER:
    group = [f for f in found if f.kind == kind]
    if not group:
        continue
    effect = sum(f.effect for f in group)
    st.subheader(f"{workbook.KIND_TITLE[kind]} ({len(group)})")
    st.caption(f"Effect on the amount owed: {ui.money(effect)}." if effect else
               "These move amounts between tabs; the amount owed does not change.")
    table = pd.DataFrame([{
        "Row": f.id, "Tab": f.year, "Series": f.series, "FA #": f.fa,
        "Tracker": workbook.describe(f.tracker), "Corrected": workbook.describe(f.corrected),
        "Effect": f.effect, "Why": f.explanation,
        "Decision": LABEL[decisions.get(f.id, {}).get("decision")], "Note": decisions.get(f.id, {}).get("note") or "",
    } for f in group])
    edited = st.data_editor(
        table, hide_index=True, width="stretch", key=f"decide_{kind}",
        disabled=["Row", "Tab", "Series", "FA #", "Tracker", "Corrected", "Effect", "Why"],
        column_config={
            "Row": None,
            "FA #": st.column_config.NumberColumn(format="%d"),
            "Tab": st.column_config.NumberColumn(format="%d"),
            "Effect": st.column_config.NumberColumn(format="dollar"),
            "Why": st.column_config.TextColumn(width="large"),
            "Decision": st.column_config.SelectboxColumn(options=list(LABEL.values()), required=True),
            "Note": st.column_config.TextColumn(width="medium"),
        })
    for r in edited.itertuples():
        new, note = CODE[r.Decision], (r.Note or None)
        cur = decisions.get(r.Row, {})
        if new != cur.get("decision") or note != (cur.get("note") or None):
            pending[r.Row] = (new, note)
    if st.button(f"Apply all {len(group)} in this group", key=f"all_{kind}"):
        n = svc.decide_tracker_rows(ui.user(), {f.id: ("apply", decisions.get(f.id, {}).get("note")) for f in group},
                                    f"Applied all: {workbook.KIND_TITLE[kind]}")
        ui.refresh()
        st.success(f"{n} row(s) set to Apply.")
        st.rerun()

st.divider()
with st.form("save_decisions"):
    st.markdown(f"**{len(pending)} unsaved decision(s)**" if pending else "**No unsaved decisions**")
    reason = st.text_input("Reason (saved to the audit log)", placeholder="e.g. Reviewed with the business owner")
    if st.form_submit_button("Save decisions", type="primary", disabled=not pending):
        if not reason:
            st.error("Add a reason so the audit log explains the decisions.")
        else:
            n = svc.decide_tracker_rows(ui.user(), pending, reason)
            ui.refresh()
            st.success(f"{n} decision(s) saved.")
            st.rerun()

st.subheader("Download the corrected workbook")
st.write("Rows marked Keep current keep the tracker's value; every other change is applied and highlighted, with a "
         "comment on each changed cell. The 'Changes from tracker' sheet lists every row and its decision.")
years = st.radio("Year tabs", ["All years", "Last three years"], horizontal=True,
                 help="Whether to keep only three years is still to be confirmed by Finance. This only changes which "
                      "tabs the workbook shows: amounts still owed on older tabs stay on the Summary, because they stay "
                      "in the accrual until they are paid or written off.")
xlsx, totals = workbook.corrected_workbook(raw, d, as_of, decisions, last_years=3 if years != "All years" else None)
st.download_button("Download corrected workbook", xlsx, type="primary",
                   file_name=f"Citigroup Accrued Expenses corrected {as_of.isoformat()}.xlsx")
st.caption(f"Corrected total {ui.money(totals['corrected_total'])}; tool accrual {ui.money(totals['ledger_total'])}.")
