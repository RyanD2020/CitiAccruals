"""Runtime configuration. Everything environment-specific comes from env vars set in app.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Settings:
    backend: str            # "databricks" or "sqlite" (sqlite is for local development/testing)
    catalog: str
    schema: str
    volume: str             # Unity Catalog volume that stores uploaded PDFs / registers
    warehouse_id: str
    sqlite_path: str
    local_files_dir: str
    schedule_start: date    # first anniversary date the tool tracks (matches the 2024 tracker tab)

    @property
    def volume_root(self) -> str:
        return f"/Volumes/{self.catalog}/{self.schema}/{self.volume}"


def load_settings() -> Settings:
    backend = os.getenv("ACCRUALS_BACKEND") or ("databricks" if os.getenv("DATABRICKS_WAREHOUSE_ID") else "sqlite")
    return Settings(
        backend=backend,
        catalog=os.getenv("ACCRUALS_CATALOG", "main"),
        schema=os.getenv("ACCRUALS_SCHEMA", "citi_accruals"),
        volume=os.getenv("ACCRUALS_VOLUME", "documents"),
        warehouse_id=os.getenv("DATABRICKS_WAREHOUSE_ID", ""),
        sqlite_path=os.getenv("ACCRUALS_SQLITE_PATH", "citi_accruals.db"),
        local_files_dir=os.getenv("ACCRUALS_LOCAL_FILES", "local_files"),
        schedule_start=date.fromisoformat(os.getenv("ACCRUALS_SCHEDULE_START", "2024-01-01")),
    )


# Citi program codes exactly as printed on the invoices ("REPACK6883 ...").
DEFAULT_PROGRAMS = [
    {
        "program_code": "REPACK6883",
        "program_name": "New York Life Global Funding $13bn Global Debt Issuance Program",
        "short_name": "GMTN",
        "annual_fee": 3000.0,
        "include_in_accrual": True,
    },
    {
        "program_code": "REPACK5970",
        "program_name": "New York Life Funding $7bn Global Debt Instrument Programme",
        "short_name": "EMTN",
        "annual_fee": 2000.0,
        # The accrual workbook only tracks GMTN. EMTN invoices are still loaded and matched;
        # switch this on from the Setup page if EMTN belongs in the reported accrual.
        "include_in_accrual": False,
    },
]

# Matching tolerances in days. Citi prints business-day-adjusted maturity dates
# (e.g. 2020-2 matures Sat 23-Jan-27 in the FA register; Citi shows Mon 25-Jan-27).
MATURITY_TOLERANCE_DAYS = 5
ACTION_DATE_TOLERANCE_DAYS = 7

# Citi bills quarterly in arrears, historically 10-25 days after quarter end.
INVOICE_EXPECTED_DAYS_AFTER_QUARTER = 25
# A fee is "overdue from Citi" once its quarter's invoice should have arrived.
FOLLOW_UP_GRACE_DAYS = 30

CITI_BILLING_EMAIL = "gatsbilling@citi.com"
