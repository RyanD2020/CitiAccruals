import re
from datetime import date

import pandas as pd
import streamlit as st

from core import engine
from core.parsers import parse_fa_register, parse_term_sheet
from views import ui

ui.header("Funding agreements", "Every FA and its fee schedule. New agreements come from a term sheet or the "
          "FA register; the schedule of annual fees is generated automatically.", eyebrow="Records")
svc = ui.service()
d = ui.data()
today = date.today()
progs = d["programs"].set_index("program_code")["short_name"]

tab_list, tab_ts, tab_reg, tab_manual = st.tabs(["Agreements", "Add from term sheet", "Sync FA register", "Add manually"])

# ------------------------------------------------------------------------------------- list
with tab_list:
    if d["contracts"].empty:
        st.info("No agreements yet. Load the folder on Setup, or add one from a term sheet.")
    else:
        lg = ui.ledger(today).frame
        c = d["contracts"].copy()
        agg = lg.groupby("fa_number").agg(
            fees=("event_id", "count"),
            paid=("status", lambda s: int((s == engine.STATUS_PAID).sum())),
            open_amt=("expected_amount", lambda s: float(s[lg.loc[s.index, "outstanding"]].sum())),
            last_billed=("invoice_no", lambda s: s.dropna().iloc[-1] if s.notna().any() else None),
        )
        c = c.merge(agg, left_on="fa_number", right_index=True, how="left")
        c["State"] = c["maturity_date"].map(lambda m: "Active" if m and m > today else "Matured")
        f1, f2, f3 = st.columns([1, 1, 2])
        prog = f1.selectbox("Program", ["All"] + list(progs.values))
        state = f2.selectbox("State", ["Active", "Matured", "All"])
        q = f3.text_input("Search series, FA or ISIN")
        view = c
        if prog != "All":
            view = view[view["program_code"].map(progs) == prog]
        if state != "All":
            view = view[view["State"] == state]
        if q:
            ql = q.lower()
            view = view[view.apply(lambda r: ql in f"{r['series']} {r['fa_number']} {r['isin'] or ''}".lower(), axis=1)]
        table = pd.DataFrame({
            "FA #": view["fa_number"].astype(int), "Series": view["series"], "Program": view["program_code"].map(progs),
            "Issue date": view["issue_date"], "Maturity": view["maturity_date"], "State": view["State"],
            "Reopening of": view["reopening_of"], "Fees tracked": view["fees"].fillna(0).astype(int),
            "Paid": view["paid"].fillna(0).astype(int), "Outstanding": view["open_amt"].fillna(0),
            "Last invoice": view["last_billed"],
        }).sort_values("FA #", ascending=False)
        st.dataframe(ui.style_col(table, "", {}, money_cols=("Outstanding",), date_cols=("Issue date", "Maturity")),
                     hide_index=True, width="stretch", height=360,
                     column_config={"FA #": st.column_config.NumberColumn(format="%d"),
                                    "Reopening of": st.column_config.NumberColumn(format="%d")})
        st.caption(f"{len(table)} agreements, {ui.money(table['Outstanding'].sum())} outstanding.")

        st.divider()
        fa = st.selectbox("Open an agreement", list(table["FA #"]),
                          format_func=lambda x: f"FA {x}, series {c.loc[c['fa_number'] == x, 'series'].iloc[0]}")
        rec = c[c["fa_number"] == fa].iloc[0]
        st.subheader(f"Series {rec['series']}, FA {fa}")
        st.write(f"{progs.get(rec['program_code'], rec['program_code'])}. Issued {rec['issue_date']:%d %B %Y}, "
                 f"matures {rec['maturity_date']:%d %B %Y}. ISIN {rec['isin'] or 'not recorded'}. "
                 f"{'Reopening of FA ' + str(int(rec['reopening_of'])) + '. ' if pd.notna(rec['reopening_of']) else ''}"
                 f"Source: {rec['source'] or 'n/a'}.")
        evs = lg[lg["fa_number"] == fa]
        et = pd.DataFrame({"Fee #": evs["period_no"], "Due": evs["event_date"], "Fee": evs["expected_amount"],
                           "Status": evs["status"], "Invoice": evs["invoice_no"], "Approved": evs["approval_date"],
                           "Paid": evs["payment_date"], "Note": evs["write_off_reason"]})
        st.dataframe(ui.style_col(et, "Status", ui.STATUS_STYLE, money_cols=("Fee",), date_cols=("Due", "Approved", "Paid")),
                     hide_index=True, width="stretch")

        e1, e2 = st.columns(2)
        with e1, st.form(f"edit_{fa}"):
            st.markdown("**Edit agreement**")
            series = st.text_input("Series (display)", value=rec["series"])
            isin = st.text_input("ISIN", value=rec["isin"] or "")
            a, b = st.columns(2)
            iss = a.date_input("Issue (settlement) date", value=rec["issue_date"], format="DD/MM/YYYY")
            mat = b.date_input("Maturity date", value=rec["maturity_date"], format="DD/MM/YYYY")
            fee = st.number_input("Annual fee override (blank uses the program fee)",
                                  value=None if pd.isna(rec["annual_fee_override"]) else float(rec["annual_fee_override"]),
                                  min_value=0.0, step=500.0)
            notes = st.text_area("Notes", value=rec["notes"] or "", height=70)
            reason = st.text_input("Reason (saved to the audit log)")
            if st.form_submit_button("Save changes", type="primary"):
                if not reason:
                    st.error("Add a reason.")
                else:
                    warn = svc.update_contract(ui.user(), int(fa), {
                        "series": series, "isin": isin or None, "issue_date": iss, "maturity_date": mat,
                        "annual_fee_override": fee, "notes": notes or None}, reason)
                    ui.refresh()
                    for w in warn:
                        st.warning(w)
                    st.success("Saved. The fee schedule was refreshed if dates or fee changed.")
                    st.rerun()
        with e2, st.form(f"wo_{fa}"):
            st.markdown("**Write off or reinstate a fee**")
            st.caption("Use when Citi confirms it will not bill a fee, or the business decides to stop accruing it.")
            cand = evs[evs["invoice_no"].isna()]
            pick = st.selectbox("Fee", list(cand["event_id"]),
                                format_func=lambda e: f"{cand.loc[cand['event_id'] == e, 'event_date'].iloc[0]:%d-%b-%Y}, "
                                                      f"{cand.loc[cand['event_id'] == e, 'status'].iloc[0]}")
            wod = st.date_input("Write-off date", value=today, format="DD/MM/YYYY")
            reason = st.text_input("Reason (saved to the audit log)", key=f"wor_{fa}")
            x, y = st.columns(2)
            do_wo = x.form_submit_button("Write off")
            do_re = y.form_submit_button("Reinstate")
            if (do_wo or do_re) and pick:
                if not reason:
                    st.error("Add a reason.")
                else:
                    try:
                        if do_wo:
                            svc.write_off(ui.user(), pick, wod, reason)
                        else:
                            svc.reinstate(ui.user(), pick, reason)
                        ui.refresh()
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))


def schedule_preview(program_code, issue, maturity, fee_override=None):
    p = d["programs"]
    fee = fee_override if fee_override else float(p.loc[p["program_code"] == program_code, "annual_fee"].iloc[0])
    sched = engine.fee_dates(issue, maturity, svc.schedule_start(d))
    return pd.DataFrame({"Fee #": [n for n, _ in sched], "Due": [x for _, x in sched], "Fee": [fee] * len(sched)})


def contract_form(prefix: str, defaults: dict, source: str, doc_bytes=None, doc_name=None):
    with st.form(f"{prefix}_form"):
        c1, c2, c3 = st.columns(3)
        fa = c1.number_input("FA number", value=int(defaults.get("fa_number") or svc.next_fa_number(d)), step=1,
                             help="Suggested as the next number in the sequence. Change it if the register says otherwise.")
        prog_codes = list(d["programs"]["program_code"])
        pcode = c2.selectbox("Program", prog_codes, index=prog_codes.index(defaults.get("program_code") or "REPACK6883"),
                             format_func=lambda x: progs.get(x, x))
        series = c3.text_input("Series (display)", value=defaults.get("series") or "",
                               help="Reopenings get a (2), (3) suffix; Citi invoices show only the base series.")
        c4, c5, c6 = st.columns(3)
        trade = c4.date_input("Trade date", value=defaults.get("trade_date"), format="DD/MM/YYYY")
        issue = c5.date_input("Issue (settlement) date", value=defaults.get("issue_date"), format="DD/MM/YYYY")
        mat = c6.date_input("Maturity date", value=defaults.get("maturity_date"), format="DD/MM/YYYY")
        c7, c8, c9 = st.columns(3)
        isin = c7.text_input("ISIN", value=defaults.get("isin") or "")
        principal = c8.text_input("Principal", value=defaults.get("principal") or "")
        reopen = c9.number_input("Reopening of FA (blank if new)", value=defaults.get("reopening_of"), step=1)
        notes = st.text_input("Notes", value=defaults.get("notes") or "")
        submitted = st.form_submit_button("Add funding agreement", type="primary")
    if issue and mat and mat > issue:
        sp = schedule_preview(pcode, issue, mat)
        st.caption(f"Fee schedule that will be created: {len(sp)} fees, {ui.money(sp['Fee'].sum())}.")
        st.dataframe(ui.style_col(sp, "", {}, money_cols=("Fee",), date_cols=("Due",)), hide_index=True,
                     width="content")
    if submitted:
        try:
            doc_path = svc.files.save("term_sheets", doc_name, doc_bytes) if doc_bytes else None
            base = re.sub(r"\s*\(\d+\)$", "", series.strip())
            svc.add_contract(ui.user(), {
                "fa_number": int(fa), "program_code": pcode, "series": series.strip(), "base_series": base,
                "reopening_of": int(reopen) if reopen else None, "isin": isin or None, "trade_date": trade,
                "issue_date": issue, "maturity_date": mat, "principal": principal or None,
                "currency": defaults.get("currency"), "source": source, "source_document": doc_path,
                "notes": notes or None}, f"New funding agreement ({source})")
            ui.refresh()
            st.success(f"FA {int(fa)} added with its fee schedule.")
        except Exception as exc:
            st.error(str(exc))


# ------------------------------------------------------------------------------- term sheet
with tab_ts:
    st.write("Upload the final term sheet. Series, dates, ISIN and principal are read from it; the FA number "
             "is suggested as the next in sequence because it is not on the term sheet.")
    up = st.file_uploader("Term sheet PDF", type=["pdf"], key="ts_upload")
    if up is not None:
        try:
            ts = parse_term_sheet(up.getvalue())
        except Exception as exc:
            st.error(f"Could not read the term sheet: {exc}")
            ts = None
        if ts:
            if ts.missing:
                st.warning("Not found on the term sheet: " + ", ".join(ts.missing) + ". Fill them in below.")
            cons = d["contracts"]
            pcode = ts.program_code or "REPACK6883"
            same = cons[(cons["program_code"] == pcode) & (cons["base_series"] == ts.series)] if ts.series else cons.iloc[0:0]
            isin_key = (ts.isin or "").split(" ")[0]
            exact = same[(same["issue_date"] == ts.settlement_date)]
            if len(exact):
                r = exact.iloc[0]
                st.success(f"This term sheet is already set up as FA {int(r['fa_number'])} (series {r['series']}, "
                           f"issued {r['issue_date']:%d-%b-%Y}). Nothing to add.")
            else:
                display, original = (ts.series, None)
                if len(same) or ts.reopening_hint:
                    display, original = svc.suggest_series_display(pcode, ts.series, d)
                    if original:
                        st.info(f"Series {ts.series} already exists (FA {original}). Treating this as a reopening: "
                                f"it gets its own FA number and its own anniversary dates.")
                elif isin_key and cons["isin"].fillna("").str.contains(isin_key, regex=False).any():
                    st.info("This ISIN is already on another agreement. Check whether this is a reopening.")
                contract_form("ts", {"program_code": pcode, "series": display, "trade_date": ts.trade_date,
                                     "issue_date": ts.settlement_date, "maturity_date": ts.maturity_date,
                                     "isin": ts.isin, "principal": ts.principal, "currency": ts.currency,
                                     "reopening_of": original, "notes": ts.security},
                              "Term sheet", up.getvalue(), up.name)

# --------------------------------------------------------------------------------- register
with tab_reg:
    st.write("Upload the latest EMTN & GMTN Funding Agreement List. New FAs and changed dates are shown for you "
             "to approve; nothing changes until you apply.")
    up = st.file_uploader("FA register (.xls or .xlsx)", type=["xls", "xlsx"], key="reg_upload")
    if up is not None:
        try:
            reg = parse_fa_register(up.getvalue(), up.name)
            diff = svc.register_diff(reg, d)
        except Exception as exc:
            st.error(f"Could not read the register: {exc}")
            diff = None
        if diff is not None:
            new, changed = diff["new"], diff["changed"]
            if new.empty and changed.empty:
                st.success("The register matches the tool. Nothing to update.")
            else:
                sel_new = pd.DataFrame()
                if len(new):
                    st.markdown(f"**{len(new)} new agreement(s)**")
                    nv = new[["fa_number", "series", "program_code", "issue_date", "maturity_date", "isin",
                              "reopening_of"]].copy()
                    nv.insert(0, "Add", True)
                    sel_new = st.data_editor(nv, hide_index=True, width="stretch", disabled=list(nv.columns[1:]),
                                             key="reg_new")
                sel_ch = pd.DataFrame()
                if len(changed):
                    st.markdown(f"**{len(changed)} changed field(s)**")
                    cv = changed.copy()
                    cv.insert(0, "Apply", True)
                    sel_ch = st.data_editor(cv, hide_index=True, width="stretch", disabled=list(cv.columns[1:]),
                                            key="reg_ch")
                reason = st.text_input("Reason (saved to the audit log)", value="FA register sync")
                if st.button("Apply selected", type="primary"):
                    doc = svc.files.save("registers", up.name, up.getvalue())
                    fas = list(sel_new.loc[sel_new["Add"], "fa_number"]) if len(sel_new) else []
                    chs = sel_ch.loc[sel_ch["Apply"]].to_dict("records") if len(sel_ch) else []
                    warn = svc.apply_register(ui.user(), reg, fas, chs, doc, reason)
                    ui.refresh()
                    for w in warn:
                        st.warning(w)
                    st.success(f"Added {len(fas)} agreement(s), applied {len(chs)} change(s).")

# ----------------------------------------------------------------------------------- manual
with tab_manual:
    st.write("For an agreement with no term sheet to hand. A reopening needs its own FA number.")
    contract_form("manual", {}, "Manual entry")
