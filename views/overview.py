from datetime import date, timedelta

import altair as alt
import streamlit as st

from core import engine
from core.config import INVOICE_EXPECTED_DAYS_AFTER_QUARTER
from core.dates import last_completed_month_end, previous_month_end, quarter_bounds, quarter_of, quarter_label
from views import ui

ui.header("Citi trustee fee accrual", "What New York Life owes Citi for annual trustee fees, what Citi has "
          "billed, and what is still outstanding.", eyebrow="Accrual")
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
due = out[out["status"] == engine.STATUS_DUE]
billed = out[out["status"].isin([engine.STATUS_INVOICED, engine.STATUS_APPROVED])]
billed_fas = set(f.loc[f["invoice_no"].notna(), "fa_number"])
never = due[~due["fa_number"].isin(billed_fas)]

# ---- reconciliation checks for the hero --------------------------------------------------
roll_start = previous_month_end(as_of)
roll = engine.rollforward(d["fee_events"], d["contracts"], d["invoices"], d["programs"], roll_start, as_of)
issues = d["data_issues"]
open_issues = issues[issues["status"] == "Open"]

inv = d["invoices"]
progs = d["programs"]
y, q = quarter_of(today)
last_q = (y - 1, 4) if q == 1 else (y, q - 1)
qs, qe = quarter_bounds(*last_q)
expected_by = qe + timedelta(days=INVOICE_EXPECTED_DAYS_AFTER_QUARTER)
invoice_watch = []  # (program, received invoice or None, fees due in the quarter)
for p in progs.itertuples():
    got = inv[(inv["program_code"] == p.program_code) & (inv["arrears_end"] == qe)]
    due_in_q = f[(f["program_code"] == p.program_code) & (f["event_date"] >= qs) & (f["event_date"] <= qe)]
    invoice_watch.append((p, got["invoice_no"].iloc[0] if len(got) else None, due_in_q))

checks = [
    ("Rollforward ties", f"{roll_start:%d %b} to {as_of:%d %b %Y}, difference {ui.money(roll['check'])}",
     "Pass" if roll["check"] == 0 else "Fail", "pass" if roll["check"] == 0 else "fail"),
    ("Open exceptions", f"{len(open_issues)} to review" if len(open_issues) else "none",
     "Follow up" if len(open_issues) else "Pass", "review" if len(open_issues) else "pass"),
]
for p, got, _ in invoice_watch:
    if got:
        checks.append((f"{quarter_label(qe)} {p.short_name} invoice", f"received, {got}", "Pass", "pass"))
    elif today > expected_by:
        checks.append((f"{quarter_label(qe)} {p.short_name} invoice", f"not received, usually by {expected_by:%d %b}",
                       "Late", "fail"))
    else:
        checks.append((f"{quarter_label(qe)} {p.short_name} invoice", f"expected by about {expected_by:%d %b %Y}",
                       "Expected", "info"))
n_never = never["fa_number"].nunique()
checks.append(("FAs Citi has never billed", f"{n_never} agreements" if n_never else "none",
               "Follow up" if n_never else "Pass", "review" if n_never else "pass"))

ui.hero(f"Accrual at {as_of:%d %B %Y}", "What we owe Citi, and why", ui.money(total),
        f"{len(out)} annual trustee fees are due and not yet paid or written off.", checks)
st.html('<div style="height: 1.25rem"></div>')
ui.steps()
st.html('<div style="height: 1.25rem"></div>')
ui.kpi_cards([
    ("Outstanding accrual", ui.money(total), f"{len(out)} fees at {as_of:%d %b %Y}"),
    ("Due, not yet invoiced by Citi", ui.money(due["expected_amount"].sum()), f"{len(due)} fees"),
    ("Invoiced, awaiting payment", ui.money(billed["expected_amount"].sum()), f"{len(billed)} fees"),
    ("Citi has never billed these FAs", str(n_never), ui.money(never["expected_amount"].sum())),
])

# ---- Quarterly invoice watch ---------------------------------------------------------------
st.subheader("Quarterly Citi invoices")
for p, got, due_in_q in invoice_watch:
    if got:
        msg = ui.chip("Received", "pass") + f" {ui.esc(p.short_name)} invoice {ui.esc(got)} for {quarter_label(qe)}"
    elif today > expected_by:
        msg = ui.chip("Late", "fail") + (f" {ui.esc(p.short_name)} invoice for {quarter_label(qe)} has not arrived "
                                         f"(usually by {expected_by:%d %b}). {len(due_in_q)} fees "
                                         f"({ui.money(due_in_q['expected_amount'].sum())}) fell due that quarter.")
    else:
        msg = ui.chip("Expected", "info") + (f" {ui.esc(p.short_name)} invoice for {quarter_label(qe)} expected by "
                                             f"about {expected_by:%d %b %Y}; {len(due_in_q)} fees "
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
        # Bar colour comes from the theme (Dark Blue in light mode, #80BAFF in dark mode).
        chart = (alt.Chart(by_year).mark_bar(size=46)
                 .encode(x=alt.X("Fee year:O", axis=alt.Axis(labelAngle=0, title=None)),
                         y=alt.Y("Amount:Q", axis=alt.Axis(format="$,.0f", title=None, grid=False)),
                         tooltip=[alt.Tooltip("Fee year:O"), alt.Tooltip("Amount:Q", format="$,.0f")])
                 .properties(height=260).configure_view(strokeWidth=0)
                 .configure_axis(labelFont="Arial", domainColor=ui.AXIS_GREY, tickColor=ui.AXIS_GREY, grid=False))
        st.altair_chart(chart, width="stretch")
    else:
        st.write("Nothing outstanding.")
with b:
    st.subheader("Fees falling due in the next 90 days")
    nxt = inc[(inc["event_date"] > as_of) & (inc["event_date"] <= as_of + timedelta(days=90)) & ~inc["written_off"]]
    if len(nxt):
        show = nxt[["event_date", "series", "fa_number", "expected_amount"]].rename(columns={
            "event_date": "Due", "series": "Series", "fa_number": "FA #", "expected_amount": "Fee"})
        st.dataframe(ui.style_col(show, "", {}, money_cols=("Fee",), date_cols=("Due",)),
                     hide_index=True, width="stretch", height=260)
        st.caption(f"{len(nxt)} fees, {ui.money(nxt['expected_amount'].sum())}")
    else:
        st.write("No fees due in the next 90 days.")

st.divider()
snaps = d["snapshots"]
me = last_completed_month_end(today)
c1, c2 = st.columns(2)
with c1:
    st.subheader("Exceptions")
    if len(open_issues):
        counts = open_issues.groupby("category").size().sort_values(ascending=False)
        for cat, n in counts.items():
            st.markdown(f"{n} &nbsp;{ui.esc(cat)}", unsafe_allow_html=True)
        st.page_link("views/exceptions.py", label="Review exceptions")
    else:
        st.write("No open exceptions.")
with c2:
    st.subheader("Month-end")
    done = snaps[snaps["as_of"] == me]
    if len(done):
        st.markdown(ui.chip("Finalized", "pass") + f" {me:%d %B %Y}: {ui.money(done['total_outstanding'].iloc[-1])}",
                    unsafe_allow_html=True)
    else:
        st.markdown(ui.chip("Open", "review") + f" {me:%d %B %Y} has not been finalized.", unsafe_allow_html=True)
    st.page_link("views/month_end.py", label="Open month-end report")
