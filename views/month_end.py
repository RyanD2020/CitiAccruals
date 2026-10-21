"""Month-end: laid out as the demo's Month-end tab (demo/Citi_Accruals_Demo.html), plus finalizing and history."""
import pandas as pd
import streamlit as st

from core import engine, reports
from core.dates import previous_month_end
from views import ui

if not ui.require_data():
    st.stop()

svc = ui.service()
d = ui.data()
as_of = st.columns([1, 4])[0].date_input("Month-end", value=ui.default_as_of(), format="DD/MM/YYYY")
prev = previous_month_end(as_of)
lg = ui.ledger(as_of)
roll = engine.rollforward(d["fee_events"], d["contracts"], d["invoices"], d["programs"], prev, as_of)
f = lg.frame
out = f[f["outstanding"]]

ui.header(f"{as_of:%B %Y} accrual", "The rollforward ties to the fee-level detail. Finalizing saves a frozen copy, so "
          "the number reported can always be reproduced.", eyebrow="Month-end report")
if roll["check"] != 0:
    st.error(f"The rollforward differs from the fee-level detail by {ui.money(roll['check'])}. Review Exceptions "
             "before finalizing.")

xlsx = reports.accrual_workbook(as_of, lg, roll, d["invoices"], d["data_issues"], ui.user())
snaps = d["snapshots"]
existing = snaps[snaps["as_of"] == as_of]

c1, c2 = st.columns([1.2, 1], gap="large")
with c1:
    rf = pd.DataFrame({
        f"Rollforward, {as_of:%B %Y}": [f"Opening accrual, {ui.fmt_date(prev)}", f"New fees due in {as_of:%B}",
                                       "Paid", "Written off", f"Closing accrual, {ui.fmt_date(as_of)}"],
        "Amount": [roll["opening"], roll["new_fees"], -roll["paid"], -roll["written_off"], roll["closing"]]})
    ui.html_table(rf, money_cols=("Amount",), total_last=True)
    b1, b2 = st.columns(2)
    b1.download_button("Download Excel report", xlsx, file_name=f"Citi_accrual_{as_of.isoformat()}.xlsx",
                       type="primary", width="stretch")
    with b2.popover("Finalize month-end", width="stretch"):
        st.write(f"Freeze the {as_of:%d %B %Y} accrual of {ui.money(roll['closing'])} with its Excel package"
                 + (" as a new version." if len(existing) else "."))
        if st.button("Finalize", type="primary"):
            sid = svc.create_snapshot(ui.user(), as_of, xlsx, roll)
            ui.refresh()
            st.success(f"Month-end saved ({sid}).")
            st.rerun()
    if len(existing):
        st.markdown(ui.chip("Finalized", "pass") + f" by {ui.esc(existing['created_by'].iloc[-1])} on "
                    f"{ui.fmt_date(existing['created_at'].iloc[-1])}: {ui.money(existing['total_outstanding'].iloc[-1])}",
                    unsafe_allow_html=True)
with c2:
    st.markdown("**By fee year**")
    by = out.groupby("fee_year")["expected_amount"].sum().reset_index()
    by.columns = ["Fee year", "Amount"]
    by["Fee year"] = by["Fee year"].astype(str)
    ui.html_table(by, money_cols=("Amount",))

# ---- opening balance: why the tracker differed (saved at the initial load) ------------------------
bridge = ui.opening_bridge()
if bridge:
    b, rows = bridge
    rows = rows.copy()
    rows["Reason"] = rows.apply(lambda r: ui.bridge_reason(r["explanation"], r["category"]), axis=1)
    cats = rows.groupby("Reason", sort=False).agg(Items=("difference", "size"), Effect=("difference", "sum")).reset_index()
    cats.loc[len(cats)] = ["Net difference", len(rows), b["explained"]]
    unexplained = round(b["difference"] - b["explained"], 2)
    detail = rows.rename(columns={"fa_number": "FA #", "series": "Series", "fee_year": "Tracker tab",
                                  "tracker_owed": "Tracker owed", "tool_owed": "Tool owed", "difference": "Difference",
                                  "explanation": "Explanation"})[
        ["FA #", "Series", "Tracker tab", "Tracker owed", "Tool owed", "Difference", "Explanation"]]
    card = ('<div class="nyl-card"><h3>Tracker to tool</h3><ul class="nyl-kv">'
            f"<li><span>Tracker Summary tab</span><span>{ui.money(b['tracker_total'])}</span></li>"
            f"<li><span>Explained differences</span><span>{ui.money(b['explained'])}</span></li>"
            f'<li><strong>Tool accrual</strong><strong class="nyl-metric" style="font-size:1.25rem">'
            f"{ui.money(b['tool_total'])}</strong></li></ul>"
            f'<p>{ui.chip("Pass", "pass") if unexplained == 0 else ui.chip("Follow up", "fail")} '
            f"Unexplained difference: {ui.money(unexplained)}</p></div>")
    st.html(
        '<div class="nyl nyl-band">'
        + ui.section_html("Opening balance", f"Why the tracker said {ui.money(b['tracker_total'])}",
                          f"The tracker's Summary tab reported {ui.money(b['tracker_total'])}. Rebuilt fee by fee from "
                          f"the FA register and every Citi invoice, the accrual is {ui.money(b['tool_total'])}. The "
                          f"{ui.money(abs(b['difference']))} difference comes from {len(rows)} items, each explained "
                          "below and logged for sign-off.")
        + '<div class="nyl-grid nyl-grid--2">'
        + ui.table_html(cats, money_cols=("Effect",), total_last=True) + card + "</div>"
        + '<div style="height:1.25rem"></div>'
        + ui.table_html(detail, money_cols=("Tracker owed", "Tool owed", "Difference"),
                        num_cols={"Tracker owed", "Tool owed", "Difference"})
        + "</div>")
else:
    st.info("The opening-balance bridge is saved when the folder is loaded on Setup. This data was loaded before "
            "that, so load the folder again (Setup > Reset all data) to see it here.")

with st.expander("Outstanding detail"):
    det = reports._detail_frame(out)
    st.dataframe(ui.style_col(det, "Status", ui.STATUS_STYLE, money_cols=("Amount",),
                              date_cols=("Issue date", "Maturity date", "Fee due date", "Approved", "Paid")),
                 hide_index=True, width="stretch", height=380,
                 column_config={"FA #": st.column_config.NumberColumn(format="%d")})

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
