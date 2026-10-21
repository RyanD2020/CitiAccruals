"""Citi Trustee Fee Accruals - Databricks App (Streamlit)."""
import os

import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
st.set_page_config(page_title="Citi Fee Accruals", page_icon=os.path.join(HERE, "assets", "nyl_logo.png"),
                   layout="wide")

from views import ui  # noqa: E402  (after set_page_config)

st.markdown(ui.CSS, unsafe_allow_html=True)
st.logo(os.path.join(HERE, "assets", "nyl_logo.png"), size="large")

pages = {
    "Accrual": [
        st.Page("views/overview.py", title="Overview", default=True),
        st.Page("views/upload_invoice.py", title="Upload Citi invoice"),
        st.Page("views/invoices.py", title="Invoices & payments"),
        st.Page("views/month_end.py", title="Month-end report"),
    ],
    "Records": [
        st.Page("views/contracts.py", title="Funding agreements"),
        st.Page("views/followup.py", title="Citi follow-up"),
        st.Page("views/exceptions.py", title="Exceptions"),
        st.Page("views/audit.py", title="Audit history"),
    ],
    "Admin": [st.Page("views/setup.py", title="Setup")],
}
nav = st.navigation(pages)

with st.sidebar:
    st.caption(f"Signed in as {ui.user()}")
    if st.button("Refresh data", use_container_width=True):
        ui.refresh()
        st.rerun()

nav.run()
