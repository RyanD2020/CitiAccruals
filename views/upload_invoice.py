import hashlib

import pandas as pd
import streamlit as st

from core.matcher import GREEN, MANUAL, PRIOR, RED, YELLOW
from core.parsers import parse_invoice
from views import ui

ui.header("Upload Citi invoice",
          "Drop in the PDF. Every fee line is matched one-to-one to a funding agreement and its anniversary: "
          "green is an exact match, yellow posts but is worth a glance, red needs your decision.")
if not ui.require_data():
    st.stop()

svc = ui.service()
files = st.file_uploader("Citi invoice PDFs", type=["pdf"], accept_multiple_files=True,
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


CHECK_KIND = {GREEN: "green", YELLOW: "yellow", RED: "red"}

for up in files:
    raw = up.getvalue()
    key, (state, result) = preview(raw)
    st.divider()
    if state == "error":
        st.error(f"{up.name}: {result}")
        continue
    inv = result.invoice
    st.subheader(f"Invoice {inv.invoice_no}")
    meta = [f"{inv.program_code}"]
    if inv.invoice_date:
        meta.append(f"dated {inv.invoice_date:%d %B %Y}")
    if inv.arrears_start:
        meta.append(f"fees due {inv.arrears_start:%d %b %Y} to {inv.arrears_end:%d %b %Y}")
    st.write(", ".join(meta) + f". Total {ui.money(inv.fee_total)}.")

    st.markdown(" ".join(ui.chip(f"{c.name}: {c.detail}", CHECK_KIND.get(c.status, "blue")) for c in result.checks),
                unsafe_allow_html=True)
    if result.blocking:
        st.error(result.blocking[0].detail + " Open it on the Invoices page to review or reverse it.")
        st.page_link("views/invoices.py", label="Go to Invoices & payments")
        continue

    counts = result.counts
    st.markdown(
        ui.chip(f"{counts[GREEN]} match", "green") + ui.chip(f"{counts[YELLOW]} review", "yellow")
        + ui.chip(f"{counts[RED]} no match", "red")
        + (ui.chip(f"{counts[PRIOR]} before tracking", "blue") if counts[PRIOR] else ""),
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
            "Citi maturity": lm.maturity_date, "Citi fee date": lm.action_date, "Billed": lm.amount,
            "Matched FA": lm.fa_number, "Our series": contracts.loc[lm.fa_number, "series"] if lm.fa_number in contracts.index else None,
            "Anniversary": exp["event_date"] if exp is not None else None,
            "Expected": lm.expected_amount, "Notes": "; ".join(lm.notes),
        })
    table = pd.DataFrame(rows)
    st.dataframe(ui.style_col(table, "Result", ui.RESULT_STYLE, money_cols=("Billed", "Expected"),
                              date_cols=("Citi maturity", "Citi fee date", "Anniversary")),
                 hide_index=True, use_container_width=True,
                 column_config={"Matched FA": st.column_config.NumberColumn(format="%d"),
                                "Notes": st.column_config.TextColumn(width="large")})

    if len(result.not_billed):
        with st.expander(f"{len(result.not_billed)} fees due in this period that Citi did not bill "
                         f"({ui.money(result.not_billed['expected_amount'].sum())})"):
            nb = result.not_billed.rename(columns={"fa_number": "FA #", "series": "Series", "event_date": "Due",
                                                   "expected_amount": "Fee"})
            st.dataframe(ui.style_col(nb, "", {}, money_cols=("Fee",), date_cols=("Due",)), hide_index=True,
                         use_container_width=True)
            st.caption("These stay in the accrual and appear on the Citi follow-up list.")

    overrides = {}
    reds = [lm for lm in result.lines if lm.status == RED]
    if reds:
        st.markdown("**Decide the red lines.** Pick the fee each line pays, or leave it unmatched. Unmatched lines "
                    "are kept on the invoice, do not reduce the accrual, and go to Exceptions.")
        open_ev = events[events["invoice_no"].isna() & ~events["written_off"]]
        for lm in reds:
            same = open_ev[open_ev["fa_number"].isin(
                d["contracts"].loc[d["contracts"]["base_series"] == lm.series, "fa_number"])]
            opts = [None] + list(dict.fromkeys(lm.candidates + list(same["event_id"])))
            labels = {None: "Leave unmatched"}
            for eid in opts[1:]:
                if eid in ev_idx.index:
                    labels[eid] = ui.event_label(ev_idx.loc[eid].to_dict())
            opts = [o for o in opts if o in labels]
            choice = st.selectbox(f"Line {lm.line_no}: series {lm.series}, {lm.action_date:%d-%b-%Y}, "
                                  f"{ui.money(lm.amount)}. {'; '.join(lm.notes)}",
                                  opts, format_func=lambda e: labels[e], key=f"ovr_{key}_{lm.line_no}")
            overrides[lm.line_no] = choice

    with st.form(f"post_{key}"):
        st.markdown("**Approval and payment**")
        c1, c2, c3, c4 = st.columns(4)
        appr = c1.date_input("Approved for payment", value=inv.approval_date, format="DD/MM/YYYY",
                             help="Pre-filled from the 'okay to pay' stamp when the PDF has one.")
        appr_by = c2.text_input("Approved by", value=inv.approved_by or ui.user_display())
        paid = c3.date_input("Payment date", value=None, format="DD/MM/YYYY",
                             help="Leave blank until the payment goes out. You can add it later on Invoices & payments.")
        ref = c4.text_input("Payment reference", placeholder="optional")
        notes = st.text_input("Notes", placeholder="optional")
        go = st.form_submit_button(f"Post invoice {inv.invoice_no}", type="primary")
    if go:
        if appr is None and paid is not None:
            st.error("Add the approval date before recording a payment.")
        else:
            try:
                counts = svc.post_invoice(ui.user(), result, file_name=up.name, file_bytes=raw,
                                          overrides=overrides, approval_date=appr,
                                          approved_by=appr_by if appr else None, payment_date=paid,
                                          payment_ref=ref or None, notes=notes or None)
                ui.refresh()
                cache.pop(key, None)
                st.success(f"Invoice {inv.invoice_no} posted: {counts.get(GREEN, 0)} matched, "
                           f"{counts.get(YELLOW, 0)} for review, {counts.get(MANUAL, 0)} matched manually, "
                           f"{counts.get(RED, 0)} unmatched.")
            except Exception as exc:
                st.error(f"Could not post invoice {inv.invoice_no}: {exc}")
