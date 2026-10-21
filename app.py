"""Citi Trustee Fee Accruals - Databricks App (Streamlit)."""
import os

import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
st.set_page_config(page_title="Citi trustee fee accruals", page_icon=os.path.join(HERE, "assets", "nyl_logo.png"),
                   layout="wide")

from views import ui  # noqa: E402  (after set_page_config)

st.html(ui.css())  # design/nyl.css, once per run; style-only HTML takes no space on the page
st.logo(os.path.join(HERE, "assets", "nyl_logo.png"), size="large")

# Every change is recorded against the signed-in user, so on Databricks the app only runs behind the company SSO.
if ui.load_settings().backend == "databricks" and not ui.sso_email():
    st.error("Your sign-in was not passed to the app, so changes can't be recorded against you. Open the app "
             "from Databricks, where you sign in with your company SSO, and try again.")
    ui.footer()
    st.stop()

# One row of pages in the order and wording of the demo (demo/Citi_Accruals_Demo.html). Setup (initial load and
# program fees) is not in the demo, so it comes last.
pages = [
    st.Page("views/overview.py", title="Overview", default=True),
    st.Page("views/upload_invoice.py", title="Upload Citi invoice"),
    st.Page("views/invoices.py", title="Invoices & payments"),
    st.Page("views/contracts.py", title="Funding agreements"),
    st.Page("views/followup.py", title="Citi follow-up"),
    st.Page("views/month_end.py", title="Month-end"),
    st.Page("views/exceptions.py", title="Exceptions"),
    st.Page("views/audit.py", title="Audit history"),
    st.Page("views/setup.py", title="Setup"),
]
nav = st.navigation(pages, position="top")

with st.sidebar:
    st.caption(f"Signed in as {ui.user()}")
    if st.button("Refresh data", width="stretch"):
        ui.refresh()
        st.rerun()

try:
    nav.run()
finally:
    ui.footer()  # also after a page calls st.stop()
