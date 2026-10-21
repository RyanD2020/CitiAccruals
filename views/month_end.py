from datetime import date

import pandas as pd
import streamlit as st

from core import engine, reports
from core.dates import last_completed_month_end, previous_month_end
from views import ui

ui.header("Month-end report", "The accrual at month-end with its rollforward. Finalizing saves a frozen copy "
          "so the reported number can always be reproduced.", eyebrow="Accrual")
if not ui.require_data():
    st.stop()

svc = ui.service()
d = ui.data()
as_of = st.date_input("Month-end", value=last_completed_month_end(date.today()), format="DD/MM/YYYY")
prev = previous_month_end(as_of)
lg = ui.ledger(as_of)
roll = engine.rollforward(d["fee_events"], d["contracts"], d["invoices"], d["programs"], prev, as_of)
f = lg.frame
out = f[f["outstanding"]]

c1, c2 = st.columns([1, 1.4])
with c1:
    ui.kpi_cards([(f"Accrual at {as_of:%d %B %Y}", ui.money(roll["closing"]), f"{len(out)} fees")], band=False)
    st.markdown(f"**Rollforward from {prev:%d %B %Y}**")
    rf = pd.DataFrame({"": ["Opening accrual", "New fees due", "Paid", "Written off", "Closing accrual"],
                       "Amount": [roll["opening"], roll["new_fees"], -roll["paid"], -roll["written_off"], roll["closing"]]})
    ui.html_table(rf, money_cols=("Amount",), total_last=True)
    st.markdown("Check against the outstanding detail: " + ui.money(roll["check"]) + " "
                + (ui.chip("Pass", "pass") if roll["check"] == 0 else ui.chip("Review exceptions", "fail")),
                unsafe_allow_html=True)
with c2:
    st.markdown("**By status**")
    bs = out.groupby("status")["expected_amount"].agg(["count", "sum"]).reset_index()
    bs.columns = ["Status", "Fees", "Amount"]
    ui.html_table(bs, money_cols=("Amount",), chips={"Status": ui.STATUS_KIND})
    st.markdown("**By fee year**")
    by = out.groupby("fee_year")["expected_amount"].agg(["count", "sum"]).reset_index()
    by.columns = ["Fee year", "Fees", "Amount"]
    by["Fee year"] = by["Fee year"].astype(str)
    by.loc[len(by)] = ["Total", int(by["Fees"].sum()), float(by["Amount"].sum())]
    ui.html_table(by, money_cols=("Amount",), total_last=True)

st.markdown("**Outstanding detail**")
det = reports._detail_frame(out)
st.dataframe(ui.style_col(det, "Status", ui.STATUS_STYLE, money_cols=("Amount",),
                          date_cols=("Issue date", "Maturity date", "Fee due date", "Approved", "Paid")),
             hide_index=True, width="stretch", height=380,
             column_config={"FA #": st.column_config.NumberColumn(format="%d")})

xlsx = reports.accrual_workbook(as_of, lg, roll, d["invoices"], d["data_issues"], ui.user_display())
snaps = d["snapshots"]
existing = snaps[snaps["as_of"] == as_of]
a, b = st.columns(2)
a.download_button("Download Excel report", xlsx, file_name=f"Citi_accrual_{as_of.isoformat()}.xlsx", type="primary")
with b:
    if len(existing):
        st.markdown(ui.chip("Finalized", "pass") + f" by {existing['created_by'].iloc[-1]} on "
                    f"{existing['created_at'].iloc[-1]:%d %b %Y}: {ui.money(existing['total_outstanding'].iloc[-1])}",
                    unsafe_allow_html=True)
    confirm = st.checkbox(f"Finalize {as_of:%d %B %Y} at {ui.money(roll['closing'])}"
                          + (" (creates a new version)" if len(existing) else ""))
    if st.button("Finalize month-end", disabled=not confirm):
        sid = svc.create_snapshot(ui.user(), as_of, xlsx, roll)
        ui.refresh()
        st.success(f"Month-end saved ({sid}).")
        st.rerun()

st.divider()
st.subheader("Finalized month-ends")
if snaps.empty:
    st.write("None yet.")
else:
    s = snaps.sort_values("created_at", ascending=False)
    for r in s.itertuples():
        cols = st.columns([1.2, 1, 1.6, 1])
        cols[0].write(f"{r.as_of:%d %B %Y}")
        cols[1].write(ui.money(r.total_outstanding))
        cols[2].write(f"{r.created_by}, {r.created_at:%d %b %Y %H:%M}")
        raw = svc.files.load(r.file_path) if r.file_path else None
        if raw:
            cols[3].download_button("Excel", raw, file_name=f"Citi_accrual_{r.as_of.isoformat()}_final.xlsx",
                                    key=f"snap_{r.snapshot_id}")
