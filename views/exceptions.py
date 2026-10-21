import pandas as pd
import streamlit as st

from core import reports
from views import ui

ui.header("Exceptions", "Anything the tool could not settle on its own: unmatched invoice lines, missing "
          "documents, and differences found when the old tracker was loaded. Resolve each with a short note.",
          eyebrow="Records")
if not ui.require_data():
    st.stop()

svc = ui.service()
d = ui.data()
issues = d["data_issues"].copy()
c1, c2 = st.columns([1, 2])
show_resolved = c1.toggle("Show resolved", value=False)
cats = sorted(issues["category"].dropna().unique())
cat = c2.multiselect("Category", cats, default=cats)
view = issues[issues["category"].isin(cat)]
if not show_resolved:
    view = view[view["status"] == "Open"]

if view.empty:
    st.success("Nothing to review.")
    st.stop()

summary = view.groupby("category").agg(Items=("issue_id", "count"), Impact=("amount_impact", "sum")).reset_index()
summary.columns = ["Category", "Items", "Amount impact"]
ui.html_table(summary, money_cols=("Amount impact",))

table = pd.DataFrame({"Category": view["category"], "Reference": view["entity_id"], "Description": view["description"],
                      "Amount impact": view["amount_impact"], "Status": view["status"],
                      "Resolution": view["resolution"], "Resolved by": view["resolved_by"]})
st.dataframe(ui.style_col(table, "", {}, money_cols=("Amount impact",)), hide_index=True, width="stretch",
             height=380, column_config={"Description": st.column_config.TextColumn(width="large")})
st.download_button("Download as Excel", reports.xlsx_from_frames({"Exceptions": table}, money_cols=("Amount impact",)),
                   file_name="Citi_accrual_exceptions.xlsx")

st.caption("Unmatched invoice lines are fixed on Invoices & payments (Change a line's match); the exception closes "
           "automatically when you do.")
st.page_link("views/invoices.py", label="Go to Invoices & payments")

open_v = view[view["status"] == "Open"]
if len(open_v):
    st.divider()
    st.subheader("Resolve")
    with st.form("resolve"):
        pick = st.multiselect("Items", list(open_v["issue_id"]),
                              format_func=lambda i: open_v.loc[open_v["issue_id"] == i, "description"].iloc[0][:140])
        everything = st.checkbox(f"Resolve all {len(open_v)} items shown with the same note")
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
