"""Overview: laid out as the demo's Overview tab (demo/Citi_Accruals_Demo.html)."""
from datetime import date, timedelta

import altair as alt
import streamlit as st

from core import engine, reports
from core.config import INVOICE_EXPECTED_DAYS_AFTER_QUARTER
from core.dates import previous_month_end, quarter_bounds, quarter_end, quarter_label, quarter_of
from core.matcher import RED
from views import ui

if not ui.require_data():
    st.stop()

as_of = st.columns([1, 4])[0].date_input("Position as of", value=ui.default_as_of(), format="DD/MM/YYYY")
d = ui.data()
lg = ui.ledger(as_of)
f = lg.frame
out = f[f["outstanding"]]
total = float(out["expected_amount"].sum())
due = out[out["status"] == engine.STATUS_DUE]
billed = out[out["status"].isin([engine.STATUS_INVOICED, engine.STATUS_APPROVED])]
fu = reports.followup_frame(lg)
fu_past = fu[~fu["Why"].str.endswith("(invoice not yet due)")]
never_past = fu_past[fu_past["Why"].str.startswith("Citi has never")]
roll = engine.rollforward(d["fee_events"], d["contracts"], d["invoices"], d["programs"], previous_month_end(as_of), as_of)
bridge = ui.opening_bridge()

# ---- reconciliation checks (chip, kind, sentence), in the demo's order --------------------------
checks = []
if bridge:
    b, rows = bridge
    unexplained = round(b["difference"] - b["explained"], 2)
    checks.append(("Pass", "pass", f"Opening balance bridge: {len(rows)} items explain {ui.money(b['explained'])}")
                  if unexplained == 0 else
                  ("Follow up", "fail", f"Opening balance bridge: {ui.money(unexplained)} not yet explained"))
else:
    checks.append(("To do", "plain", "Opening balance bridge: load the folder on Setup to build it"))
checks.append(("Pass", "pass", f"Rollforward ties to fee detail (difference {ui.money(roll['check'])})")
              if roll["check"] == 0 else
              ("Follow up", "fail", f"Rollforward differs from fee detail by {ui.money(roll['check'])}"))
n_inv = len(d["invoices"])
n_red = int((d["invoice_lines"]["match_status"] == RED).sum())
checks.append(("Pass", "pass", f"{n_inv} invoices loaded; every line matched, none red") if not n_red else
              ("Follow up", "fail", f"{n_inv} invoices loaded; {n_red} lines need a decision"))
# The quarter whose Citi invoice comes next: the as-of quarter once it has ended, otherwise the one before.
y, q = quarter_of(as_of)
if as_of != quarter_end(as_of):
    y, q = (y - 1, 4) if q == 1 else (y, q - 1)
qs, qe = quarter_bounds(y, q)
expected_by = qe + timedelta(days=INVOICE_EXPECTED_DAYS_AFTER_QUARTER)
in_scope = d["programs"].loc[d["programs"]["include_in_accrual"], "program_code"]
received = d["invoices"][d["invoices"]["program_code"].isin(in_scope) & (d["invoices"]["arrears_end"] == qe)]
if len(received) >= len(in_scope):
    checks.append(("Pass", "pass", f"{quarter_label(qe)} Citi invoice received ({', '.join(received['invoice_no'])})"))
elif date.today() > expected_by:
    checks.append(("Late", "fail", f"{quarter_label(qe)} Citi invoice not received; usually here by {expected_by:%d %B}"))
else:
    checks.append(("Expected", "review", f"{quarter_label(qe)} Citi invoice due by about {expected_by:%d %B}"))
n_never = never_past["FA #"].nunique()
checks.append(("Follow up", "fail", f"{n_never} FAs Citi has never billed in the tracked period") if n_never else
              ("Pass", "pass", "Citi has billed every FA with a fee past its invoice window"))

text = f"{len(out)} annual trustee fees are due and not yet paid."
if bridge:
    b, _ = bridge
    gap = round(b["difference"] - b["explained"], 2)
    text += (f" The old tracker reported {ui.money(b['tracker_total'])}; every dollar of the "
             f"{ui.money(abs(b['difference']))} difference is explained." if gap == 0 else
             f" The old tracker reported {ui.money(b['tracker_total'])}; {ui.money(abs(gap))} of the difference is "
             f"not yet explained.")
ui.hero(f"Accrual at {as_of:%d %B %Y}", "What we owe Citi, and why", ui.money(total), text, checks)

# ---- how it works -------------------------------------------------------------------------------
st.html('<div class="nyl nyl-section">' + ui.section_html("How it works", "From Citi invoice to Finance in four steps")
        + ui.steps_html() + "</div>")

# ---- KPIs ---------------------------------------------------------------------------------------
ui.kpi_cards([
    ("Outstanding accrual", ui.money(total), f"{len(out)} fees at {as_of:%d %b %Y}"),
    ("Due, not yet invoiced", ui.money(due["expected_amount"].sum()), f"{len(due)} fees"),
    ("Invoiced, awaiting payment", ui.money(billed["expected_amount"].sum()),
     f"{len(billed)} fees" if len(billed) else "All loaded invoices are paid"),
    ("Overdue from Citi", ui.money(fu_past["Expected fee"].sum()), f"{len(fu_past)} fees past their invoice window"),
])

# ---- aging and Citi's next invoice ---------------------------------------------------------------
a, b2 = st.columns([1.2, 1], gap="large")
with a:
    ui.section("Aging", "Outstanding by fee year")
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
with b2:
    ny, nq = (y + 1, 1) if q == 4 else (y, q + 1)
    ns, ne = quarter_bounds(ny, nq)
    inc = f[f["include_in_accrual"]]
    nxt = inc[(inc["event_date"] >= ns) & (inc["event_date"] <= ne) & ~inc["written_off"]].sort_values(["event_date", "series"])
    show = nxt[["event_date", "series", "fa_number", "expected_amount"]].rename(columns={
        "event_date": "Due", "series": "Series", "fa_number": "FA #", "expected_amount": "Fee"})
    st.html('<div class="nyl">' + ui.section_html(
        "Citi's next invoice", f"Fees falling due {ns:%B} to {ne:%B %Y}",
        f"{len(nxt)} fees, {ui.money(nxt['expected_amount'].sum())}. These join the accrual on their due dates.", level=3)
        + (ui.table_html(show, money_cols=("Fee",), date_cols=("Due",), num_cols={"Fee"}) if len(nxt) else "")
        + "</div>")
