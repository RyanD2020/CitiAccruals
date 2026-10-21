# Deploying the Citi fee accruals app

Releases run entirely inside Databricks. A job named **citi-fee-accruals release** runs three notebooks from
the deployment Git folder, each starting only if the previous one succeeded:

| Task | Notebook | What it does |
|---|---|---|
| `pull` | `notebooks/pipeline/01_pull.py` | Updates the Git folder to the latest commit on `main` (or to a tag) and records the commit hash |
| `test` | `notebooks/pipeline/02_test.py` | Installs `requirements.txt`, runs `tests/test_core.py` on the test documents with SQLite, checks the regression baseline |
| `deploy` | `notebooks/pipeline/03_deploy.py` | Deploys the app from the Git folder, waits until it is RUNNING, prints the URL and commit |

If any task fails, the later tasks don't run, the app keeps its current deployment, and you get an email.

The test task fails unless all of these hold: 12 PDF invoices posted; 145 green, 36 yellow and 0 red lines;
6 "prior" lines, all on invoice 127932; $177,000 outstanding at 2026-09-30; an opening-balance bridge of
29 items totalling −$27,000; a September 2026 rollforward check of 0; next FA number 70751. It prints only
these counts and totals. It never prints invoice lines or document text, even when it fails.

The notebooks use databricks-sdk 0.122.0, which is preinstalled in serverless environment version 6. The
job pins that version. The same calls also exist in 0.49.0 and 0.67.0. Each notebook checks the method
signatures when it starts and stops with a clear message if the SDK doesn't match.

In the steps below, replace `<CATALOG>` with your catalog. The Git folder is assumed to be
`/Workspace/Shared/citi_accruals`. If you use a different path, change it everywhere it appears in
`notebooks/pipeline/release_job.json`.

---

## One-time setup

### 1. Point `app.yaml` at your catalog

In `app.yaml`, set `ACCRUALS_CATALOG` to `<CATALOG>` and merge that to `main`. The deploy task refuses to
deploy if `app.yaml` names a different catalog from the job's `catalog` parameter.

### 2. Create the schema, volume and tables

In a SQL editor, run `sql/schema.sql` after replacing `your_catalog` with `<CATALOG>`. Leave the `GRANT`
statements at the bottom for step 6. You need `CREATE SCHEMA` on the catalog for this step.

### 3. Put the test documents in the volume

In Catalog Explorer, go to `<CATALOG>` > `citi_accruals` > Volumes > `documents`. Create a folder named
`test_fixtures` and upload `Citi_Accruals_Documents.zip` into it. The job reads
`/Volumes/<CATALOG>/citi_accruals/documents/test_fixtures/Citi_Accruals_Documents.zip`.

The documents live only in the volume. They are never committed to git: `.gitignore` and `databricks.yml`
exclude every zip, PDF and Excel file.

### 4. Create the deployment Git folder

1. Go to Workspace > Shared > Create > Git folder.
2. Enter `https://github.com/RyanD2020/CitiAccruals.git`, choose branch `main`, and name the folder
   `citi_accruals`.
3. Treat the folder as read-only. Nobody edits files in it. `01_pull` fails if the folder has uncommitted
   changes, and that's deliberate. Do development in your own Git folder under your user directory.
4. Under Share, keep CAN MANAGE for yourself only. Pulling and switching branches need CAN MANAGE on a Git
   folder, so the job's run-as identity must have it. Give everyone else CAN READ or no access.

### 5. Create the app

1. Go to Compute > Apps > Create app > Custom app. Name it `citi-fee-accruals`.
2. Add a resource: **SQL warehouse**, resource key `sql-warehouse`, permission **Can use**. `app.yaml`
   reads it through `valueFrom: sql-warehouse`.
3. Create the app. You don't need to deploy anything yet; the job does that.
4. On the app's page, note its **service principal**: the name and the application ID.
5. Give that service principal **CAN READ** on the Git folder from step 4 (Git folder > Share). The app
   deploys from that folder.
6. Give business users **Can use** on the app (app > Permissions). Give **Can manage** to anyone who
   should be able to start the app when it has stopped.

### 6. Grant the app's service principal access to its data

Run the grants at the end of `sql/schema.sql`, with `<app-sp>` replaced by the service principal's
application ID from step 5:

```sql
GRANT USE CATALOG ON CATALOG <CATALOG> TO `<app-sp>`;
GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY ON SCHEMA <CATALOG>.citi_accruals TO `<app-sp>`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME <CATALOG>.citi_accruals.documents TO `<app-sp>`;
```

The job runs as you, so you also need `READ VOLUME` on `documents` to read the test zip, and CAN MANAGE on
the app to deploy it. As the app's creator you already have CAN MANAGE.

### 7. Create the job

You can create it from the JSON (option A) or by hand in the UI (option B).

**Option A: from `release_job.json` (recommended)**

First update the Git folder to the commit that contains `notebooks/pipeline`. Then run this in any notebook
on serverless compute:

```python
import json
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import JobSettings

text = open("/Workspace/Shared/citi_accruals/notebooks/pipeline/release_job.json").read()
text = text.replace("<CATALOG>", "your_catalog_name")           # <- your catalog
assert "<" not in text, "a placeholder is still in the job definition"
job = WorkspaceClient().jobs.create(**JobSettings.from_dict(json.loads(text)).as_shallow_dict())
print("job_id", job.job_id)
```

This sends exactly the JSON in the file to the Jobs API (`POST /api/2.2/jobs/create`).

**Option B: in the UI**

1. Go to Jobs & Pipelines > Create > Job. Name it `citi-fee-accruals release`.
2. **Task `pull`.** Type Notebook, Source Workspace, path
   `/Workspace/Shared/citi_accruals/notebooks/pipeline/01_pull`, compute **Serverless**. Under Environment,
   set the environment version to **6**.
3. **Task `test`.** Add a task the same way with notebook `02_test`, environment version 6, and
   *Depends on* `pull`.
4. **Task `deploy`.** Add a task the same way with notebook `03_deploy`, environment version 6, and
   *Depends on* `test`.
5. **Job parameters** (Job details panel > Parameters):

   | Name | Default |
   |---|---|
   | `git_folder_path` | `/Workspace/Shared/citi_accruals` |
   | `branch` | `main` |
   | `tag` | *(empty)* |
   | `commit` | *(empty)* |
   | `app_name` | `citi-fee-accruals` |
   | `catalog` | `<CATALOG>` |
   | `test_zip` | `/Volumes/<CATALOG>/citi_accruals/documents/test_fixtures/Citi_Accruals_Documents.zip` |

6. **Run as**: leave it as yourself (the default).
7. **Notifications**: Add > Failure > your email.
8. **Advanced settings**: Maximum concurrent runs 1, Queue on.

Run the job once to check it. The `deploy` task output ends with the app URL, the deployed commit and the
deployment ID.

---

## Release routine

1. Merge the change to `main` on GitHub.
2. Go to Jobs & Pipelines > **citi-fee-accruals release** > **Run now**.
3. When it finishes, open the `deploy` task output for the app URL and the deployed commit. If it fails, the
   email links to the run; open the red task to see why.
4. Optional, but it makes rollback one click: tag the commit you just released. On your machine:
   `git tag release-2026-10-21 <commit> && git push origin release-2026-10-21`.

---

## Rollback

Git folders can only be moved to a **branch or a tag**. The Repos API (`ReposAPI.update`) has no way to
check out a commit. So a rollback names a tag (or branch) that points at the good commit, and passes the
commit hash so the run fails if the tag points somewhere else.

1. Find the good commit. It's printed in the `deploy` output of the last good run (job run history).
2. If that commit has no tag yet, create one on your machine:
   `git tag rollback-<first 7 chars> <commit> && git push origin rollback-<first 7 chars>`.
3. Go to the job > Run now with different parameters. Set `tag` to the tag name and `commit` to the hash.
   Leave `branch` as it is.
4. The run checks out the tag, confirms the Git folder is at that commit, re-runs the regression test on
   that code, and deploys it.
5. Fix forward on `main`. The next normal **Run now** (with `tag` empty) moves the Git folder back to `main`.

Alternatively, `git revert` the bad change on `main` and release normally.

---

## Switching "run as" to a service principal

The job currently runs as Ryan. Jobs started with Run now use the run-as identity's permissions, not those
of the person who clicks. To move it to a service principal:

1. Settings > Identity and access > Service principals > Add. Name it, for example,
   `citi-accruals-release`.
2. Give it:
   - CAN MANAGE on the Git folder (required to pull and switch branches)
   - CAN MANAGE on the app (required to deploy)
   - `USE CATALOG` on `<CATALOG>`, `USE SCHEMA` on `<CATALOG>.citi_accruals`, and `READ VOLUME` on
     `<CATALOG>.citi_accruals.documents` (to read the test zip)
   - If the GitHub repo is private, a GitHub credential under the service principal's Git credentials
3. Give yourself the **Service principal: User** role on it, so you can set it as a job's run-as.
4. On the job, set Run as to the service principal. Update `run_as` in `notebooks/pipeline/release_job.json`
   (`{"service_principal_name": "<application-id>"}`) and in `databricks.yml` to match.

---

## Moving to external CI later

`databricks.yml` already declares the app (`citi_accruals_app`) and this job (`citi_accruals_release`). The
bundle has not been validated yet, because the Databricks CLI wasn't available when it was written. Run
`databricks bundle validate -t prod` before anything else.

When a GitHub Actions or Azure DevOps pipeline is added:

1. Fill in `<WORKSPACE_URL>`, `<CATALOG>` and `<WAREHOUSE_ID>` in `databricks.yml`.
2. Bind the resources that already exist, so the bundle takes them over instead of creating duplicates:
   ```
   databricks bundle deployment bind citi_accruals_app citi-fee-accruals -t prod
   databricks bundle deployment bind citi_accruals_release <job-id> -t prod
   ```
3. On merge to `main`, the pipeline authenticates as the service principal (OAuth client ID and secret
   stored as pipeline secrets, or workload identity federation) and runs:
   ```
   databricks bundle validate -t prod
   databricks bundle deploy -t prod
   databricks bundle run citi_accruals_release -t prod
   ```
   The job stays the release engine (pull, test, deploy), so nothing is restructured. The pipeline only
   keeps the job and app definitions in sync with git and presses Run now.
4. Pull-request checks need the test documents, which only exist in the volume. Run them as a test-only
   job inside Databricks (a second Git folder on the PR branch, running `pull` and `test` without
   `deploy`). Don't copy the documents to the CI runner.
