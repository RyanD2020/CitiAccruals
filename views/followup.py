from datetime import date

import streamlit as st

from core import reports
from views import ui

ui.header("Citi follow-up", "Fees that are due but Citi has not invoiced. Sending this list each quarter is how "
          "missing invoices get caught before they turn into multi-year write-offs.", eyebrow="Records")
if not ui.require_data():
    st.stop()

svc = ui.service()
today = date.today()
c1, c2, c3 = st.columns([1, 1, 2])
as_of = c1.date_input("As of", value=today, format="DD/MM/YYYY")
overdue_only = c2.toggle("Only past the invoice window", value=True,
                         help="Hide fees whose quarterly invoice is not due yet.")
lg = ui.ledger(as_of)
fu_all = reports.followup_frame(lg)
progs = sorted(fu_all["Program"].dropna().unique())
prog = c3.multiselect("Programs", progs, default=progs)
fu = fu_all[fu_all["Program"].isin(prog)]
if overdue_only:
    fu = fu[~fu["Why"].str.endswith("(invoice not yet due)")]

never = fu[fu["Why"].str.startswith("Citi has never billed")]
a, b, c = st.columns(3)
a.metric("Fees to chase", f"{len(fu)}", ui.money(fu["Expected fee"].sum()), delta_color="off")
b.metric("FAs Citi has never billed", f"{never['FA #'].nunique()}", ui.money(never["Expected fee"].sum()),
         delta_color="off")
c.metric("Oldest unbilled fee", f"{int(fu['Days since due'].max())} days" if len(fu) else "None")

st.dataframe(ui.style_col(fu, "", {}, money_cols=("Expected fee",),
                          date_cols=("Fee due date", "Issue date", "Maturity date"))
             .map(lambda v: ui.cell_style("fail") if isinstance(v, str) and v.startswith("Citi has never") else "",
                  subset=["Why"]),
             hide_index=True, width="stretch", height=420,
             column_config={"FA #": st.column_config.NumberColumn(format="%d")})

x, y = st.columns(2)
with x:
    st.download_button("Download follow-up list (Excel)",
                       reports.xlsx_from_frames({"Citi follow-up": fu}, money_cols=("Expected fee",),
                                                date_cols=("Fee due date", "Issue date", "Maturity date")),
                       file_name=f"Citi_follow_up_{as_of.isoformat()}.xlsx", type="primary",
                       disabled=fu.empty)
with y:
    with st.expander("Email draft to Citi billing"):
        st.text_area("Copy into Outlook", reports.followup_email(fu, ui.user_display()), height=260)

st.divider()
with st.expander("Write off fees Citi has confirmed it will not bill"):
    st.write("Written-off fees leave the accrual from the write-off date. Each one is logged with your reason and can "
             "be reinstated from Funding agreements or reverted from Audit history.")
    ev = lg.frame
    pool = ev[ev["event_id"].isin(set(ev.loc[ev["status"] == "Due - not invoiced", "event_id"]))
              & ev["fa_number"].isin(fu["FA #"])]
    with st.form("bulk_wo"):
        pick = st.multiselect("Fees", list(pool["event_id"]), format_func=lambda e: ui.event_label(
            pool.loc[pool["event_id"] == e].iloc[0].to_dict()))
        wod = st.date_input("Write-off date", value=today, format="DD/MM/YYYY")
        reason = st.text_input("Reason (saved to the audit log)")
        if st.form_submit_button("Write off selected"):
            if not pick or not reason:
                st.error("Choose fees and give a reason.")
            else:
                for e in pick:
                    svc.write_off(ui.user(), e, wod, reason)
                ui.refresh()
                st.success(f"{len(pick)} fee(s) written off.")
                st.rerun()
