"""Exceptions: laid out as the demo's Exceptions tab (demo/Citi_Accruals_Demo.html), plus resolving."""
import pandas as pd
import streamlit as st

from core import reports
from views import ui

OPENING = "Opening balance reconciliation"

ui.header("What the tool could not settle on its own",
          "Found while loading the folder. Each is resolved with a note, which goes to the audit log.",
          eyebrow="Exceptions")
if not ui.require_data():
    st.stop()

svc = ui.service()
d = ui.data()
issues = d["data_issues"].copy()
cats = sorted(issues["category"].dropna().unique())
c1, c2 = st.columns([1, 3])
show_resolved = c1.toggle("Show resolved", value=False)
# The opening-balance items are explained on Month-end, so they are left out here unless chosen.
cat = c2.multiselect("Category", cats, default=[c for c in cats if c != OPENING])
view = issues[issues["category"].isin(cat)]
if not show_resolved:
    view = view[view["status"] == "Open"]

if view.empty:
    st.success("Nothing to review.")
else:
    table = pd.DataFrame({"Category": view["category"], "Reference": view["entity_id"],
                          "Description": view["description"], "Status": view["status"]})
    ui.html_table(table, chips={"Status": {"Open": "review", "Resolved": "pass"}}, num_cols=set())
n_opening = int(((issues["category"] == OPENING) & (issues["status"] == "Open")).sum())
if n_opening and OPENING not in cat:
    st.caption(f"Plus {n_opening} opening-balance items from the Month-end page, which can be resolved together "
               "once the business signs off.")
st.caption("Unmatched invoice lines are fixed on Invoices & payments (Change a line's match); the exception closes "
           "automatically when you do.")

if not view.empty:
    full = pd.DataFrame({"Category": view["category"], "Reference": view["entity_id"],
                         "Description": view["description"], "Amount impact": view["amount_impact"],
                         "Status": view["status"], "Resolution": view["resolution"],
                         "Resolved by": view["resolved_by"]})
    st.download_button("Download as Excel", reports.xlsx_from_frames({"Exceptions": full}, money_cols=("Amount impact",)),
                       file_name="Citi_accrual_exceptions.xlsx")

open_all = issues[issues["status"] == "Open"]
if len(open_all):
    st.divider()
    st.subheader("Resolve")
    resolve_cats = st.multiselect("Categories to resolve", cats, default=cat, key="resolve_cats")
    open_v = open_all[open_all["category"].isin(resolve_cats)]
    with st.form("resolve"):
        pick = st.multiselect("Items", list(open_v["issue_id"]),
                              format_func=lambda i: open_v.loc[open_v["issue_id"] == i, "description"].iloc[0][:140])
        everything = st.checkbox(f"Resolve all {len(open_v)} items in these categories with the same note")
        note = st.text_input("Resolution note (saved to the audit log)",
                             placeholder="e.g. Reviewed with the business; tool figure accepted")
        if st.form_submit_button("Mark resolved", type="primary"):
            ids = list(open_v["issue_id"]) if everything else pick
            if not ids or not note:
                st.error("Choose items and add a note.")
            else:
                for i in ids:
                    svc.resolve_issue(ui.user(), i, note)
                ui.refresh()
                st.success(f"{len(ids)} item(s) resolved.")
                st.rerun()
