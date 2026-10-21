import pandas as pd
import streamlit as st

from core import reports
from core.parsers import classify_file, unpack_zip
from views import ui

ui.header("Load the folder and set the fees", "Load the existing folder once, then adjust program fees and "
          "accrual scope here.", eyebrow="Setup")
svc = ui.service()
d = ui.data()
s = svc.settings

# --------------------------------------------------------------------------- initial load
st.subheader("Initial load")
if not d["contracts"].empty:
    st.markdown(ui.chip("Loaded", "pass") + f" {len(d['contracts'])} agreements, {len(d['fee_events'])} fees, "
                f"{len(d['invoices'])} invoices.", unsafe_allow_html=True)
    last = st.session_state.get("load_report")
else:
    st.write("Upload the whole Citi accruals folder as a .zip, or select all the files. The tool recognises each "
             "file: the FA register becomes the list of agreements, every Citi invoice PDF is matched and posted, "
             "and the old tracker is used for approval dates and to explain any difference in the opening balance.")
    ups = st.file_uploader("Folder (.zip) or files", type=["zip", "pdf", "xlsx", "xls"], accept_multiple_files=True)
    files = []
    for u in ups or []:
        if u.name.lower().endswith(".zip"):
            files += unpack_zip(u.getvalue())
        else:
            files.append((u.name, u.getvalue()))
    if files:
        cl = [classify_file(n, b) for n, b in files]
        kinds = pd.DataFrame([{"File": c.name, "Recognised as": c.kind.replace("_", " "), "Note": c.detail} for c in cl])
        st.dataframe(kinds, hide_index=True, width="stretch")
        has_reg = any(c.kind == "fa_register" for c in cl)
        has_trk = any(c.kind == "tracker" for c in cl)
        if not (has_reg or has_trk):
            st.error("Include the FA register or the accrual tracker workbook.")
        elif st.button("Load everything", type="primary"):
            with st.spinner("Reading documents, building fee schedules and matching invoices..."):
                try:
                    rep = svc.initial_load(ui.user(), files)
                    st.session_state["load_report"] = rep
                    ui.refresh()
                except Exception as exc:
                    st.error(f"Load failed: {exc}")
            st.rerun()
    last = st.session_state.get("load_report")

if last:
    st.markdown("**Load results**")
    inv = last["invoices"].rename(columns={"invoice": "Invoice", "program": "Program", "date": "Date", "total": "Total",
                                           "lines": "Lines", "green": "Match", "yellow": "Review", "red": "No match",
                                           "prior": "Before tracking", "manual": "Manual"})
    st.dataframe(ui.style_col(inv, "", {}, money_cols=("Total",), date_cols=("Date",)), hide_index=True,
                 width="stretch")
    if "bridge_summary" in last:
        b = last["bridge_summary"]
        st.markdown(f"**Opening balance at {b['as_of']:%d %B %Y}.** The tracker reported {ui.money(b['tracker_total'])}; "
                    f"the tool calculates {ui.money(b['tool_total'])}. Difference {ui.money(b['difference'])}, of which "
                    f"{ui.money(b['explained'])} is explained line by line below and logged in Exceptions for sign-off.")
        br = last["bridge"]
        if len(br):
            grp = br.groupby("category")["difference"].agg(["count", "sum"]).reset_index()
            grp.columns = ["Category", "Items", "Amount"]
            grp.loc[len(grp)] = ["Total", int(grp["Items"].sum()), float(grp["Amount"].sum())]
            ui.html_table(grp, money_cols=("Amount",), total_last=True)
            show = br.rename(columns={"fa_number": "FA #", "series": "Series", "fee_year": "Tracker tab",
                                      "tracker_owed": "Tracker owed", "tool_owed": "Tool owed", "difference": "Difference",
                                      "category": "Category", "explanation": "Explanation"})
            st.dataframe(ui.style_col(show, "", {}, money_cols=("Tracker owed", "Tool owed", "Difference")),
                         hide_index=True, width="stretch",
                         column_config={"FA #": st.column_config.NumberColumn(format="%d"),
                                        "Explanation": st.column_config.TextColumn(width="large")})
            st.download_button("Download opening balance bridge (Excel)",
                               reports.xlsx_from_frames({"Bridge": show, "Invoices loaded": inv},
                                                        money_cols=("Tracker owed", "Tool owed", "Difference", "Total")),
                               file_name="Citi_opening_balance_bridge.xlsx")

st.divider()

# ----------------------------------------------------------------------------- programs
st.subheader("Programs and fees")
st.write("Changing a fee reprices every fee not yet invoiced. Accrual scope controls which programs count toward "
         "the reported accrual; invoices for all programs are still matched and stored.")
p = d["programs"][["program_code", "short_name", "program_name", "annual_fee", "include_in_accrual"]].copy()
edited = st.data_editor(p, hide_index=True, width="stretch", disabled=["program_code"],
                        column_config={"program_code": "Citi code", "short_name": "Name", "program_name": "Program",
                                       "annual_fee": st.column_config.NumberColumn("Annual fee", format="$%d"),
                                       "include_in_accrual": "In accrual"}, key="prog_ed", num_rows="dynamic")
reason = st.text_input("Reason (saved to the audit log)", key="prog_reason")
if st.button("Save programs"):
    if not reason:
        st.error("Add a reason.")
    else:
        cur = set(d["programs"]["program_code"])
        for r in edited.to_dict("records"):
            if not r.get("program_code"):
                continue
            if r["program_code"] in cur:
                svc.update_program(ui.user(), r["program_code"], {k: r[k] for k in
                                   ("short_name", "program_name", "annual_fee", "include_in_accrual")}, reason)
            else:
                from core.services import now
                svc.db.insert("programs", [{**r, "updated_by": ui.user(), "updated_at": now()}])
                svc.audit(ui.user(), "create", "programs", r["program_code"], None, None, r, reason)
        ui.refresh()
        st.success("Programs saved.")
        st.rerun()

st.divider()
st.subheader("Environment")
st.write(f"Storage: {'Unity Catalog ' + s.catalog + '.' + s.schema if s.backend == 'databricks' else 'local SQLite ' + s.sqlite_path}. "
         f"Documents: {s.volume_root if s.backend == 'databricks' else s.local_files_dir}. "
         f"Tracking starts {svc.schedule_start(d):%d %B %Y} (fees due before this are informational only).")

with st.expander("Reset all data"):
    st.write("Deletes every table's rows so the folder can be loaded again. Uploaded documents stay in storage. "
             "Use only before go-live.")
    confirm = st.text_input("Type RESET to confirm")
    if st.button("Delete all data", disabled=confirm != "RESET"):
        svc.db.truncate_all()
        svc.ensure_defaults(ui.user())
        svc.audit(ui.user(), "reset", "system", "all", None, None, None, "All data reset from Setup")
        st.session_state.pop("load_report", None)
        ui.refresh()
        st.rerun()
