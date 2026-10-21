"""Invoices & payments: the invoice list as in the demo's Invoices tab, with the payment and line tools below."""
import pandas as pd
import streamlit as st

from core.matcher import MANUAL, RED, YELLOW
from core.parsers import file_hash, parse_invoice
from views import ui

if not ui.require_data():
    st.stop()

svc = ui.service()
d = ui.data()
inv = d["invoices"].copy()
lines = d["invoice_lines"]
if inv.empty:
    ui.header("No Citi invoices yet", eyebrow="Invoices & payments")
    st.page_link("views/upload_invoice.py", label="Upload a Citi invoice")
    st.stop()


def inv_status(r):
    if r["payment_date"] is not None:
        return "Paid"
    if r["approval_date"] is not None:
        return "Approved"
    return "Awaiting approval"


n_red = int((lines["match_status"] == RED).sum())
missing = inv[inv["file_path"].isna()]["invoice_no"].tolist()
headline = (f"{ui.count_words(len(inv))} Citi invoices, every line accounted for" if not n_red else
            f"{ui.count_words(len(inv))} Citi invoices, {n_red} lines need a decision")
lede = "All invoices from the folder were loaded and matched."
if missing:
    lede += (f" Invoice {missing[0]} had no PDF, so it was rebuilt from the tracker and flagged." if len(missing) == 1
             else f" Invoices {', '.join(missing)} had no PDF, so they were rebuilt from the tracker and flagged.")
ui.header(headline, lede + " Select an invoice to see its lines.", eyebrow="Invoices & payments")

cnt = lines.pivot_table(index="invoice_no", columns="match_status", values="line_id", aggfunc="count", fill_value=0)
inv = inv.merge(cnt, left_on="invoice_no", right_index=True, how="left")
for c in ("green", "yellow", "red", "manual", "prior"):
    if c not in inv:
        inv[c] = 0
inv["Status"] = inv.apply(inv_status, axis=1)
progs = d["programs"].set_index("program_code")["short_name"]
show = inv.sort_values("invoice_date", ascending=False).reset_index(drop=True)


def result_text(r) -> str:
    parts = [(r["green"], "match"), (r["yellow"], "review"), (r["manual"], "manual match"), (r["red"], "no match"),
             (r["prior"], "before tracking")]
    return ", ".join(f"{int(n)} {t}" for n, t in parts if pd.notna(n) and n)


table = pd.DataFrame({
    "Invoice": show["invoice_no"], "Program": show["program_code"].map(progs), "Invoice date": show["invoice_date"],
    "Fees due": show.apply(lambda r: f"{ui.fmt_date(r['arrears_start'])} to {ui.fmt_date(r['arrears_end'])}"
                           if r["arrears_start"] else "", axis=1),
    "Total": show["fee_total"], "Lines": show["line_count"], "Result": show.apply(result_text, axis=1),
    "Status": show["Status"], "PDF": show["file_path"].notna().map({True: "On file", False: "Missing"}),
})
STATUS = {"Paid": ui.cell_style("pass"), "Approved": ui.cell_style("info"), "Awaiting approval": ui.cell_style("info")}
picked = st.dataframe(ui.style_col(table, "Status", STATUS, money_cols=("Total",), date_cols=("Invoice date",))
                      .map(lambda v: ui.cell_style("fail") if isinstance(v, str) and "no match" in v else "",
                           subset=["Result"]),
                      hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key="invoice_table")
sel = picked.selection.rows if picked is not None else []

unpaid = inv[inv["payment_date"].isna()]
if len(unpaid):
    with st.expander(f"Record a payment for several invoices ({len(unpaid)} unpaid)"):
        with st.form("bulk_pay"):
            pick = st.multiselect("Invoices", list(unpaid["invoice_no"]))
            c1, c2 = st.columns(2)
            pdate = c1.date_input("Payment date", value=None, format="DD/MM/YYYY")
            pref = c2.text_input("Payment reference", placeholder="optional")
            reason = st.text_input("Reason (saved to the audit log)", value="Payment recorded")
            if st.form_submit_button("Record payment", type="primary"):
                if not pick or pdate is None:
                    st.error("Choose at least one invoice and a payment date.")
                else:
                    for no in pick:
                        row = inv[inv["invoice_no"] == no].iloc[0]
                        ch = {"payment_date": pdate, "payment_ref": pref or row["payment_ref"]}
                        if row["approval_date"] is None:
                            ch.update({"approval_date": pdate, "approved_by": ui.user()})
                        svc.update_invoice(ui.user(), no, ch, reason)
                    ui.refresh()
                    st.success(f"Payment recorded on {len(pick)} invoice(s).")
                    st.rerun()

if not sel:
    st.caption("Select an invoice above to see its lines.")
    st.stop()

choice = show.loc[sel[0], "invoice_no"]
row = inv[inv["invoice_no"] == choice].iloc[0]
st.subheader(f"Invoice {choice}")
st.write(f"{progs.get(row['program_code'], row['program_code'])}, total {ui.money(row['fee_total'])}, "
         f"{int(row['line_count'] or 0)} lines. {row['notes'] or ''}")

il = lines[lines["invoice_no"] == choice].sort_values("line_no")
ev = d["fee_events"].merge(d["contracts"][["fa_number", "series"]], on="fa_number", how="left").set_index("event_id")
lt = pd.DataFrame({
    "Line": il["line_no"], "Result": il["match_status"].map(ui.RESULT_LABEL), "Citi series": il["series"],
    "Fee date": il["action_date"], "Amount": il["amount"], "FA #": il["fa_number"],
    "Our series": il["event_id"].map(lambda e: ev.loc[e, "series"] if e in ev.index else ""),
    "Notes": il["match_notes"],
})
st.dataframe(ui.style_col(lt, "Result", ui.RESULT_STYLE, money_cols=("Amount",), date_cols=("Fee date",)),
             hide_index=True, width="stretch",
             column_config={"FA #": st.column_config.NumberColumn(format="%d"),
                            "Notes": st.column_config.TextColumn(width="large")})

with st.expander("Approval and payment", expanded=row["payment_date"] is None):
    with st.form(f"dates_{choice}"):
        appr = st.date_input("Approved for payment", value=row["approval_date"], format="DD/MM/YYYY")
        st.caption(f"Approved by {row['approved_by']}." if row["approved_by"] else "Not approved yet.")
        b1, b2 = st.columns(2)
        paid = b1.date_input("Payment date", value=row["payment_date"], format="DD/MM/YYYY")
        ref = b2.text_input("Payment reference", value=row["payment_ref"] or "")
        notes = st.text_input("Notes", value=row["notes"] or "")
        reason = st.text_input("Reason (saved to the audit log)", placeholder="e.g. payment confirmed by AP")
        if st.form_submit_button("Save", type="primary"):
            if not reason:
                st.error("Add a reason so the audit log explains the change.")
            elif paid is not None and appr is None:
                st.error("Add the approval date before recording a payment.")
            else:
                # The approver is whoever records the approval (their SSO sign-in); unchanged if the date is.
                appr_by = (None if appr is None else row["approved_by"] if appr == row["approval_date"]
                           else ui.user())
                changed = svc.update_invoice(ui.user(), choice, {
                    "approval_date": appr, "approved_by": appr_by, "payment_date": paid,
                    "payment_ref": ref or None, "notes": notes or None}, reason)
                ui.refresh()
                st.success(f"Saved {len(changed)} change(s)." if changed else "Nothing changed.")
                st.rerun()

with st.expander("Source PDF"):
    raw = svc.files.load(row["file_path"]) if row["file_path"] else None
    if raw:
        st.download_button("Download PDF", raw, file_name=row["file_name"] or f"{choice}.pdf", mime="application/pdf")
    else:
        st.write("No PDF on file for this invoice.")
        up = st.file_uploader("Attach the PDF", type=["pdf"], key=f"attach_{choice}")
        if up is not None:
            try:
                parsed = parse_invoice(up.getvalue())
                if parsed.invoice_no != choice:
                    st.error(f"That PDF is invoice {parsed.invoice_no}, not {choice}.")
                else:
                    path = svc.files.save(f"invoices/{choice}", up.name, up.getvalue())
                    svc.update_invoice(ui.user(), choice, {"file_name": up.name, "file_path": path,
                                                           "file_hash": file_hash(up.getvalue())}, "PDF attached")
                    if abs((parsed.fee_total or 0) - (row["fee_total"] or 0)) > 0.005:
                        st.warning(f"PDF total {ui.money(parsed.fee_total)} differs from the recorded "
                                   f"{ui.money(row['fee_total'])}. Consider reversing and re-uploading.")
                    ui.refresh()
                    st.success("PDF attached.")
            except Exception as exc:
                st.error(f"Could not read the PDF: {exc}")

fixable = il[il["match_status"].isin([RED, YELLOW, MANUAL])]
if len(fixable):
    with st.expander("Change a line's match"):
        ln = st.selectbox("Line", list(fixable["line_id"]),
                          format_func=lambda x: f"Line {int(il.loc[il['line_id'] == x, 'line_no'].iloc[0])}: "
                                                f"{il.loc[il['line_id'] == x, 'series'].iloc[0]}")
        line = il[il["line_id"] == ln].iloc[0]
        base = d["contracts"].loc[d["contracts"]["base_series"] == line["series"], "fa_number"]
        cands = ev[(ev["invoice_no"].isna() | (ev["invoice_no"] == choice)) & ev["fa_number"].isin(base)]
        opts = [None] + list(cands.index)
        pick = st.selectbox("Fee this line pays", opts, format_func=lambda e: "Leave unmatched" if e is None
                            else ui.event_label(ev.loc[e].to_dict()))
        r = st.text_input("Reason (saved to the audit log)", key="line_reason")
        if st.button("Save match"):
            if not r:
                st.error("Add a reason.")
            else:
                try:
                    svc.resolve_line(ui.user(), ln, pick, r)
                    ui.refresh()
                    st.success("Line updated.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

with st.expander("Reverse this invoice"):
    st.write("Removes the invoice and its lines and puts every fee it paid back into the accrual. The full "
             "posting is kept in the audit log. Use this if the wrong PDF was uploaded, then upload it again.")
    r = st.text_input("Reason (saved to the audit log)", key="rev_reason")
    sure = st.checkbox(f"I want to reverse invoice {choice}")
    if st.button("Reverse invoice", disabled=not (sure and r)):
        svc.reverse_invoice(ui.user(), choice, r)
        ui.refresh()
        st.success(f"Invoice {choice} reversed.")
        st.rerun()
