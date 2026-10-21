"""Upload Citi invoice: laid out as the demo's Upload tab (demo/Citi_Accruals_Demo.html)."""
import hashlib

import pandas as pd
import streamlit as st

from core.matcher import GREEN, MANUAL, PRIOR, RED, YELLOW
from core.parsers import parse_invoice
from views import ui

ui.header("Every line matched one to one",
          "Green is an exact match. Yellow posts but is worth a glance, usually a weekend maturity Citi moved to "
          "Monday. Red means the numbers don't match or there is no clean match, and needs a decision.",
          eyebrow="Upload Citi invoice")
if not ui.require_data():
    st.stop()

svc = ui.service()
files = st.file_uploader("Drop a Citi invoice PDF here", type=["pdf"], accept_multiple_files=True,
                         help="You can drop several invoices at once; each is reviewed and posted separately.")
if not files:
    st.stop()

cache = st.session_state.setdefault("invoice_previews", {})


def preview(raw: bytes):
    # Re-match whenever the ledger changes (another invoice posted, a contract edited, ...).
    version = len(ui.data()["audit_log"])
    key = f"{hashlib.sha256(raw).hexdigest()[:16]}_{version}"
    if key not in cache:
        try:
            cache[key] = ("ok", svc.match(parse_invoice(raw), ui.data()))
        except Exception as exc:
            cache[key] = ("error", str(exc))
    return key, cache[key]


# Invoice-level checks: green Pass, yellow Note, red Fail (as in the demo)
CHECK_CHIP = {GREEN: ("Pass", "pass"), YELLOW: ("Note", "review"), RED: ("Fail", "fail")}

for up in files:
    raw = up.getvalue()
    key, (state, result) = preview(raw)
    st.divider()
    if state == "error":
        st.error(f"{up.name}: {result}")
        continue
    inv = result.invoice
    st.subheader(f"Invoice {inv.invoice_no}")
    meta = []
    if inv.invoice_date:
        meta.append(f"Dated {ui.fmt_date(inv.invoice_date)}")
    if inv.arrears_start:
        meta.append(f"fees due {ui.fmt_date(inv.arrears_start)} to {ui.fmt_date(inv.arrears_end)}")
    st.write(", ".join(meta) + ("." if meta else "") + f" Total {ui.money(inv.fee_total)}.")

    st.html('<div class="nyl-checkcards">' + "".join(
        f'<div class="nyl-checkcard">{ui.chip(*CHECK_CHIP.get(c.status, ("Note", "review")))}'
        f"<span><strong>{ui.esc(c.name)}.</strong> {ui.esc(c.detail)}</span></div>" for c in result.checks) + "</div>")
    if result.blocking:
        st.error(result.blocking[0].detail + " Open it on Invoices & payments to review or reverse it.")
        st.page_link("views/invoices.py", label="Go to Invoices & payments")
        continue

    counts = result.counts
    st.markdown(
        ui.chip(f"{counts[GREEN]} match", "match") + " " + ui.chip(f"{counts[YELLOW]} review", "review") + " "
        + ui.chip(f"{counts[RED]} no match", "nomatch")
        + (" " + ui.chip(f"{counts[PRIOR]} before tracking", "plain") if counts[PRIOR] else ""),
        unsafe_allow_html=True)

    d = ui.data()
    contracts = d["contracts"].set_index("fa_number")
    events = d["fee_events"].merge(d["contracts"][["fa_number", "series"]], on="fa_number", how="left")
    ev_idx = events.set_index("event_id")
    rows = []
    for lm in result.lines:
        exp = ev_idx.loc[lm.event_id] if lm.event_id in ev_idx.index else None
        rows.append({
            "Line": lm.line_no, "Result": ui.RESULT_LABEL[lm.status], "Citi series": lm.series,
            "Citi fee date": lm.action_date, "Billed": lm.amount, "Matched FA": lm.fa_number,
            "Our series": contracts.loc[lm.fa_number, "series"] if lm.fa_number in contracts.index else None,
            "Anniversary": exp["event_date"] if exp is not None else None,
            "Expected": lm.expected_amount, "Notes": " ".join(lm.notes),
        })
    table = pd.DataFrame(rows)
    st.dataframe(ui.style_col(table, "Result", ui.RESULT_STYLE, money_cols=("Billed", "Expected"),
                              date_cols=("Citi fee date", "Anniversary")),
                 hide_index=True, width="stretch",
                 column_config={"Matched FA": st.column_config.NumberColumn(format="%d"),
                                "Notes": st.column_config.TextColumn(width="large")})

    nb = result.not_billed
    if len(nb):
        ui.notice(f"<strong>{len(nb)} fees due in this period are not on the invoice "
                  f"({ui.money(nb['expected_amount'].sum())}).</strong> They stay in the accrual and go on the Citi "
                  "follow-up list: " + "; ".join(
                      f"{ui.esc(r.series)} (FA {int(r.fa_number)}, due {ui.fmt_date(r.event_date)})"
                      for r in nb.itertuples()) + ".")

    overrides = {}
    reds = [lm for lm in result.lines if lm.status == RED]
    if reds:
        ui.section(None, "Decide the red lines",
                   "Pick the fee each line pays, or leave it unmatched. Unmatched lines stay on the invoice, "
                   "don't reduce the accrual, and go to Exceptions.")
        open_ev = events[events["invoice_no"].isna() & ~events["written_off"]]
        for lm in reds:
            same = open_ev[open_ev["fa_number"].isin(
                d["contracts"].loc[d["contracts"]["base_series"] == lm.series, "fa_number"])]
            opts = [None] + list(dict.fromkeys(([lm.event_id] if lm.event_id else []) + lm.candidates
                                               + list(same["event_id"])))
            labels = {None: "Leave unmatched"}
            for eid in opts[1:]:
                if eid in ev_idx.index:
                    e = ev_idx.loc[eid]
                    if eid == lm.event_id:  # the fee the matcher found, as in the demo
                        labels[eid] = f"Accept: {e['series']}, FA {int(e['fa_number'])}, due {ui.fmt_date(e['event_date'])}"
                    else:
                        labels[eid] = "Match to " + ui.event_label(e.to_dict())
            opts = [o for o in opts if o in labels]
            st.html(f"<div>{ui.chip('No match', 'nomatch')} <strong>Line {lm.line_no}:</strong> series "
                    f"{ui.esc(lm.series)}, {ui.fmt_date(lm.action_date)}, {ui.money(lm.amount)}. "
                    f'<span class="nyl-muted">{ui.esc(" ".join(lm.notes))}</span></div>')
            choice = st.selectbox(f"Line {lm.line_no}", opts, format_func=lambda e: labels[e],
                                  key=f"ovr_{key}_{lm.line_no}", label_visibility="collapsed")
            overrides[lm.line_no] = choice

    with st.form(f"post_{key}"):
        st.markdown("**Approval and payment**")
        c1, c2, c3, c4 = st.columns(4)
        appr = c1.date_input("Approved for payment", value=inv.approval_date, format="DD/MM/YYYY")
        c2.text_input("Approved by", value=ui.user(), disabled=True,
                      help="Recorded from your company sign-in.")
        paid = c3.date_input("Payment date", value=None, format="DD/MM/YYYY")
        ref = c4.text_input("Payment reference", placeholder="optional")
        st.caption('The approval date is pre-filled from the "okay to pay" stamp when the PDF has one. Leave the '
                   "payment date blank until the money goes out.")
        go = st.form_submit_button(f"Post invoice {inv.invoice_no}", type="primary")
    if go:
        if appr is None and paid is not None:
            st.error("Add the approval date before recording a payment.")
        else:
            try:
                counts = svc.post_invoice(ui.user(), result, file_name=up.name, file_bytes=raw,
                                          overrides=overrides, approval_date=appr,
                                          approved_by=ui.user() if appr else None, payment_date=paid,
                                          payment_ref=ref or None)
                ui.refresh()
                cache.pop(key, None)
                left = counts.get(RED, 0)
                st.success(
                    f"Invoice {inv.invoice_no} posted. {counts.get(GREEN, 0)} matched, {counts.get(YELLOW, 0)} for "
                    f"review, {counts.get(MANUAL, 0)} matched manually, {left} left unmatched"
                    + (" and sent to Exceptions. " if left else ". ")
                    + (f"Payment dated {ui.fmt_date(paid)}: these fees leave the accrual." if paid else
                       "No payment date yet, so the fees show as approved and awaiting payment." if appr else
                       "No approval date yet, so the fees show as invoiced and awaiting approval."))
            except Exception as exc:
                st.error(f"Could not post invoice {inv.invoice_no}: {exc}")
