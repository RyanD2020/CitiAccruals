"""Citi follow-up: laid out as the demo's Citi follow-up tab (demo/Citi_Accruals_Demo.html)."""
from datetime import date

import streamlit as st

from core import reports
from views import ui

ui.header("Fees Citi has not billed", "Sent to Citi each quarter, this list catches missing invoices before they turn "
          "into multi-year write-offs.", eyebrow="Citi follow-up")
if not ui.require_data():
    st.stop()

svc = ui.service()
c1, c2, c3, c4 = st.columns([1, 1.3, 1.3, 1.4])
as_of = c1.date_input("As of", value=ui.default_as_of(), format="DD/MM/YYYY")
overdue_only = c2.toggle("Only fees past their invoice window", value=True,
                         help="Hide fees whose quarterly invoice is not due yet.")
never_only = c3.toggle("Only FAs Citi has never billed", value=False)
lg = ui.ledger(as_of)
fu_all = reports.followup_frame(lg)
progs = sorted(fu_all["Program"].dropna().unique())
prog = c4.multiselect("Programs", progs, default=progs)
fu = fu_all[fu_all["Program"].isin(prog)]
if overdue_only:
    fu = fu[~fu["Why"].str.endswith("(invoice not yet due)")]
if never_only:
    fu = fu[fu["Why"].str.startswith("Citi has never billed")]

never = fu[fu["Why"].str.startswith("Citi has never billed")]
ui.kpi_cards([
    ("Fees to chase", str(len(fu)), ui.money(fu["Expected fee"].sum())),
    ("FAs Citi has never billed", str(never["FA #"].nunique()), ui.money(never["Expected fee"].sum())),
    ("Oldest unbilled fee", f"{int(fu['Days since due'].max())} days" if len(fu) else "None",
     "since it fell due" if len(fu) else ""),
], band=False)

NEVER = "Citi has never billed this FA in the tracked period"
show = fu[["Program", "Series", "FA #", "Maturity date", "Fee due date", "Expected fee", "Days since due", "Why"]].rename(
    columns={"Maturity date": "Maturity", "Fee due date": "Fee due", "Expected fee": "Fee"})
show["Why"] = show["Why"].map(lambda w: "Never billed" + w[len(NEVER):] if w.startswith(NEVER) else w)
st.dataframe(ui.style_col(show, "", {}, money_cols=("Fee",), date_cols=("Maturity", "Fee due"))
             .map(lambda v: ui.cell_style("fail") if isinstance(v, str) and v.startswith("Never billed") else "",
                  subset=["Why"]),
             hide_index=True, width="stretch", height=420,
             column_config={"FA #": st.column_config.NumberColumn(format="%d")})

x, y = st.columns([1, 1.6], gap="large")
with x:
    ui.section(None, "Email draft to Citi billing", "Generated from the list above, ready to paste into Outlook. "
               "Use the copy button on the draft.")
    st.download_button("Download Excel",
                       reports.xlsx_from_frames({"Citi follow-up": fu}, money_cols=("Expected fee",),
                                                date_cols=("Fee due date", "Issue date", "Maturity date")),
                       file_name=f"Citi_follow_up_{as_of.isoformat()}.xlsx", type="primary", disabled=fu.empty)
with y:
    # st.code shows the draft with a copy-to-clipboard button. Signed "[Your name]" as in the demo.
    st.code(reports.followup_email(fu, "[Your name]"), language=None, wrap_lines=True)

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
        wod = st.date_input("Write-off date", value=date.today(), format="DD/MM/YYYY")
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
