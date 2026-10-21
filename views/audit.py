from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core import reports
from views import ui

ui.header("Audit history", "Every change, who made it, when, and why. Field changes can be put back with one click; "
          "invoice postings are undone with Reverse invoice.", eyebrow="Records")
svc = ui.service()
log = ui.data()["audit_log"].sort_values("ts", ascending=False)
if log.empty:
    st.info("No activity yet.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
start = c1.date_input("From", value=date.today() - timedelta(days=90), format="DD/MM/YYYY")
end = c2.date_input("To", value=date.today(), format="DD/MM/YYYY")
users = sorted(log["user_name"].dropna().unique())
who = c3.multiselect("User", users)
q = c4.text_input("Search (FA, invoice, field, reason)")
v = log[(log["ts"].dt.date >= start) & (log["ts"].dt.date <= end)]
if who:
    v = v[v["user_name"].isin(who)]
if q:
    ql = q.lower()
    v = v[v.apply(lambda r: ql in " ".join(str(x) for x in r.values).lower(), axis=1)]

table = pd.DataFrame({"When": v["ts"], "User": v["user_name"], "Action": v["action"], "Record": v["entity"],
                      "Key": v["entity_id"], "Field": v["field"], "Old value": v["old_value"],
                      "New value": v["new_value"], "Reason": v["reason"]})
st.dataframe(table, hide_index=True, width="stretch", height=420,
             column_config={"When": st.column_config.DatetimeColumn(format="DD MMM YYYY HH:mm"),
                            "Old value": st.column_config.TextColumn(width="medium"),
                            "New value": st.column_config.TextColumn(width="medium")})
st.download_button("Download as Excel", reports.xlsx_from_frames({"Audit": table.assign(When=table["When"].astype(str))}),
                   file_name="Citi_accrual_audit.xlsx")

st.divider()
st.subheader("Put a change back")
rev = v[v["action"].isin(["update", "write_off", "reinstate"]) & v["field"].notna()
        & ~v["entity"].isin(["audit_log"])].head(300)
if rev.empty:
    st.write("No field changes in this view.")
else:
    with st.form("revert"):
        pick = st.selectbox("Change", list(rev["audit_id"]), format_func=lambda a: (
            lambda r: f"{r['ts']:%d %b %Y %H:%M}, {r['user_name']}: {r['entity']} {r['entity_id']} {r['field']} "
                      f"{r['old_value']} to {r['new_value']}")(rev.loc[rev["audit_id"] == a].iloc[0]))
        reason = st.text_input("Reason (saved to the audit log)")
        if st.form_submit_button("Revert this change", type="primary"):
            if not reason:
                st.error("Add a reason.")
            else:
                try:
                    st.success(svc.revert(ui.user(), pick, reason))
                    ui.refresh()
                except Exception as exc:
                    st.error(str(exc))
st.caption("The audit log is permanent. The underlying Delta tables also keep version history, so an administrator "
           "can restore a whole table to an earlier point if ever needed (DESCRIBE HISTORY / RESTORE TABLE).")
