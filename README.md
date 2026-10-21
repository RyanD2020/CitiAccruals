# Citi Trustee Fee Accruals (Databricks App)

A Streamlit app on Databricks that replaces the "Citigroup Accrued Expenses" workbook. It builds the
annual fee schedule for every funding agreement (FA), matches each Citi invoice line one-to-one to a
fee, tracks approval and payment, and produces the month-end accrual with a full audit trail.

## Deploy

1. **Create the schema and volume** (or let the app try on first start). Run `sql/schema.sql` in a SQL
   editor after replacing `your_catalog`. It also contains the grants the app's service principal needs:
   `USE CATALOG`, `USE SCHEMA`, `CREATE TABLE`, `SELECT`, `MODIFY` on the schema, and `READ/WRITE VOLUME`.
2. **Create the app.** In the workspace go to Compute > Apps > Create app > Custom, and upload this
   folder (or sync it from a Git folder).
3. **Add a resource.** On the app, add a SQL warehouse resource with key `sql-warehouse` and
   permission *Can use*. A small serverless warehouse is enough.
4. **Edit `app.yaml`.** Set `ACCRUALS_CATALOG` (and `ACCRUALS_SCHEMA` / `ACCRUALS_VOLUME` if different).
5. **Deploy**, then give the business users *Can use* on the app (and *Can manage* to anyone who should
   be able to start it when it has stopped).
6. **Load the folder.** Open Setup, upload the Citi accruals zip (FA register, tracker workbook and all
   invoice PDFs), and click *Load everything*. Review the opening-balance bridge and resolve the items
   on the Exceptions page once the business has signed off.

`requirements.txt` installs from PyPI at deploy time. If your workspace blocks PyPI, ask the platform
team for the internal mirror.

## Day-to-day

| When | Where | What happens |
|---|---|---|
| Citi invoice arrives (about quarterly) | Upload Citi invoice | PDF is parsed, every line matched green / yellow / red, approval date pre-filled from the "okay to pay" stamp, posted in one click |
| Payment goes out | Invoices & payments | Enter the payment date once for the invoice (or several invoices) |
| New issue or reopening | Funding agreements > Add from term sheet | Series, dates, ISIN read from the PDF; next FA number suggested; reopenings detected; fee schedule generated |
| Register updated | Funding agreements > Sync FA register | New FAs and changed dates shown for approval |
| Each quarter | Citi follow-up | Fees due but not billed, with Excel export and email draft for gatsbilling@citi.com |
| Month-end | Month-end report | Accrual, rollforward, Excel package; *Finalize* freezes a copy |

## Matching colours

- **Green**: one-to-one match; series, maturity, anniversary date and amount all agree.
- **Yellow**: one-to-one match with the right amount, but a date differs (Citi prints weekend maturities
  as the next business day) or the line was rebuilt without a PDF. Posts normally.
- **Red**: amounts differ or there is no clean one-to-one match (unknown series, no fee due on that date,
  already billed elsewhere, duplicate line). You choose the fee or leave it unmatched; unmatched lines do
  not reduce the accrual and go to Exceptions.

## Fee rules (core/engine.py)

- Flat annual fee per FA: $3,000 GMTN (REPACK6883), $2,000 EMTN (REPACK5970). Editable on Setup.
- Due on the issue (settlement) date and every anniversary; none on an anniversary on or after maturity.
- No proration: a short final period carries the full fee, as Citi bills it.
- Reopenings are separate FAs with their own anniversaries. Citi invoices show only the base series,
  so matching uses series + anniversary date.
- A fee is in the accrual from its due date until paid or written off. EMTN is tracked but excluded from
  the accrual by default (Setup > Programs > "In accrual").

## Audit and recovery

Every change is written to `audit_log` with the user's email (from Databricks Apps sign-in), old and new
values, and a reason. Field changes can be reverted from Audit history; invoice postings are undone with
*Reverse invoice*. Delta history (`DESCRIBE HISTORY`, `RESTORE TABLE`) is available to administrators.

## Local development

```bash
pip install -r requirements.txt
ACCRUALS_BACKEND=sqlite streamlit run app.py      # uses ./citi_accruals.db and ./local_files
python -m tests.test_core path/to/folder.zip      # end-to-end check against real documents
```

## Layout

```
app.py                 navigation and branding
app.yaml               Databricks Apps config
core/parsers.py        Citi invoice, term sheet, FA register and tracker parsers (no AI, pattern based)
core/engine.py         fee schedule, statuses, accrual, rollforward
core/matcher.py        one-to-one green/yellow/red matching
core/services.py       every workflow, all writes audited
core/db.py             Unity Catalog (SQL warehouse) and SQLite backends
core/reports.py        Excel outputs (summary totals are live formulas)
views/                 one file per page
sql/schema.sql         reference DDL and grants
```
