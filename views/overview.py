from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from core import engine
from core.config import INVOICE_EXPECTED_DAYS_AFTER_QUARTER
from core.dates import last_completed_month_end, quarter_bounds, quarter_of, quarter_label
from views import ui

ui.header("Citi trustee fee accrual", "What New York Life owes Citi for annual trustee fees, what Citi has "
          "billed, and what is still outstanding.")
if not ui.require_data():
    st.stop()

today = date.today()
as_of = st.date_input("Position as of", value=today, format="DD/MM/YYYY")
d = ui.data()
lg = ui.ledger(as_of)
f = lg.frame
inc = f[f["include_in_accrual"]]
out = inc[inc["outstanding"]]
total = out["expected_amount"].sum()

left, right = st.columns([1.15, 2])
with left:
    st.markdown(f'<div class="nyl-big">{ui.money(total)}</div>'
                f'<div class="nyl-big-label">outstanding accrual on {as_of:%d %B %Y}, '
                f'{len(out)} fees</div>', unsafe_allow_html=True)
with right:
    c1, c2, c3 = st.columns(3)
    due = out[out["status"] == engine.STATUS_DUE]
    billed = out[out["status"].isin([engine.STATUS_INVOICED, engine.STATUS_APPROVED])]
    billed_fas = set(f.loc[f["invoice_no"].notna(), "fa_number"])
    never = due[~due["fa_number"].isin(billed_fas)]
    c1.metric("Due, not yet invoiced by Citi", ui.money(due["expected_amount"].sum()), f"{len(due)} fees",
              delta_color="off")
    c2.metric("Invoiced, awaiting payment", ui.money(billed["expected_amount"].sum()), f"{len(billed)} fees",
              delta_color="off")
    c3.metric("Citi has never billed these FAs", f"{never['fa_number'].nunique()}",
              ui.money(never["expected_amount"].sum()), delta_color="off")

st.divider()

# ---- Quarterly invoice watch ---------------------------------------------------------------
st.subheader("Quarterly Citi invoices")
inv = d["invoices"]
progs = d["programs"]
y, q = quarter_of(today)
last_q = (y - 1, 4) if q == 1 else (y, q - 1)
qs, qe = quarter_bounds(*last_q)
rows = []
for p in progs.itertuples():
    got = inv[(inv["program_code"] == p.program_code) & (inv["arrears_end"] == qe)]
    expected_by = qe + timedelta(days=INVOICE_EXPECTED_DAYS_AFTER_QUARTER)
    due_in_q = f[(f["program_code"] == p.program_code) & (f["event_date"] >= qs) & (f["event_date"] <= qe)]
    if len(got):
        msg = ui.chip("Received", "green") + f" {p.short_name} invoice {got['invoice_no'].iloc[0]} for {quarter_label(qe)}"
    elif today > expected_by:
        msg = ui.chip("Late", "red") + (f" {p.short_name} invoice for {quarter_label(qe)} has not arrived "
                                        f"(usually by {expected_by:%d %b}). {len(due_in_q)} fees "
                                        f"({ui.money(due_in_q['expected_amount'].sum())}) fell due that quarter.")
    else:
        msg = ui.chip("Expected", "yellow") + (f" {p.short_name} invoice for {quarter_label(qe)} expected by about "
                                               f"{expected_by:%d %b %Y}; {len(due_in_q)} fees "
                                               f"({ui.money(due_in_q['expected_amount'].sum())}) are due on it.")
    st.markdown(msg, unsafe_allow_html=True)
st.page_link("views/upload_invoice.py", label="Upload a Citi invoice")

st.divider()
a, b = st.columns([1.3, 1])
with a:
    st.subheader("Outstanding by fee year")
    if len(out):
        by_year = out.groupby("fee_year")["expected_amount"].sum().reset_index()
        by_year.columns = ["Fee year", "Amount"]
        chart = (alt.Chart(by_year).mark_bar(color=ui.DARK_BLUE, size=46)
                 .encode(x=alt.X("Fee year:O", axis=alt.Axis(labelAngle=0, title=None)),
                         y=alt.Y("Amount:Q", axis=alt.Axis(format="$,.0f", title=None, grid=False)),
                         tooltip=[alt.Tooltip("Fee year:O"), alt.Tooltip("Amount:Q", format="$,.0f")])
                 .properties(height=260).configure_view(strokeWidth=0)
                 .configure_axis(labelFont="Arial", labelColor="#000", domainColor="#888888", tickColor="#888888"))
        st.altair_chart(chart, use_container_width=True)
    else:
        st.write("Nothing outstanding.")
with b:
    st.subheader("Fees falling due in the next 90 days")
    nxt = inc[(inc["event_date"] > as_of) & (inc["event_date"] <= as_of + timedelta(days=90)) & ~inc["written_off"]]
    if len(nxt):
        show = nxt[["event_date", "series", "fa_number", "expected_amount"]].rename(columns={
            "event_date": "Due", "series": "Series", "fa_number": "FA #", "expected_amount": "Fee"})
        st.dataframe(ui.style_col(show, "", {}, money_cols=("Fee",), date_cols=("Due",)),
                     hide_index=True, use_container_width=True, height=260)
        st.caption(f"{len(nxt)} fees, {ui.money(nxt['expected_amount'].sum())}")
    else:
        st.write("No fees due in the next 90 days.")

st.divider()
issues = d["data_issues"]
open_issues = issues[issues["status"] == "Open"]
snaps = d["snapshots"]
me = last_completed_month_end(today)
c1, c2 = st.columns(2)
with c1:
    st.subheader("Exceptions")
    if len(open_issues):
        counts = open_issues.groupby("category").size().sort_values(ascending=False)
        for cat, n in counts.items():
            st.markdown(f"{n} &nbsp;{cat}", unsafe_allow_html=True)
        st.page_link("views/exceptions.py", label="Review exceptions")
    else:
        st.write("No open exceptions.")
with c2:
    st.subheader("Month-end")
    done = snaps[snaps["as_of"] == me]
    if len(done):
        st.markdown(ui.chip("Finalized", "green") + f" {me:%d %B %Y}: {ui.money(done['total_outstanding'].iloc[-1])}",
                    unsafe_allow_html=True)
    else:
        st.markdown(ui.chip("Open", "yellow") + f" {me:%d %B %Y} has not been finalized.", unsafe_allow_html=True)
    st.page_link("views/month_end.py", label="Open month-end report")
