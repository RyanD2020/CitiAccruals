# Databricks notebook source
# MAGIC %md
# MAGIC # 03 Deploy
# MAGIC Deploys the app from the Git folder, waits until the deployment has succeeded and the app is RUNNING,
# MAGIC then prints the app URL and the deployed commit. On failure prints the deployment status message and
# MAGIC fails the task.
# MAGIC
# MAGIC | Parameter | Meaning |
# MAGIC |---|---|
# MAGIC | `git_folder_path` | Deployment Git folder (the app's source code path) |
# MAGIC | `app_name` | `citi-fee-accruals` |
# MAGIC | `catalog` | Catalog the app must point at; checked against `ACCRUALS_CATALOG` in `app.yaml` |

# COMMAND ----------

import inspect
import re
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import (AppDeployment, AppDeploymentMode, AppDeploymentState, ApplicationState,
                                         ComputeState)
from databricks.sdk.version import __version__ as sdk_version

dbutils.widgets.text("git_folder_path", "")
dbutils.widgets.text("app_name", "citi-fee-accruals")
dbutils.widgets.text("catalog", "")

git_folder_path = dbutils.widgets.get("git_folder_path").strip().rstrip("/")
app_name = dbutils.widgets.get("app_name").strip()
catalog = dbutils.widgets.get("catalog").strip()
commit = dbutils.jobs.taskValues.get(taskKey="pull", key="commit", default="", debugValue="")

if not git_folder_path.startswith("/Workspace/"):
    raise ValueError("git_folder_path must be the full path, e.g. /Workspace/Shared/citi_accruals")
if not app_name or not catalog:
    raise ValueError("Set the app_name and catalog job parameters.")

DEPLOY_TIMEOUT_S = 20 * 60
START_TIMEOUT_S = 20 * 60
RUNNING_TIMEOUT_S = 10 * 60

# COMMAND ----------

# Calls below were checked against databricks-sdk 0.49.0, 0.67.0 and 0.122.0 (serverless environments 3-6).
w = WorkspaceClient()
print(f"databricks-sdk {sdk_version}")
expected = {
    w.workspace.get_status: {"path"},
    w.repos.get: {"repo_id"},
    w.apps.get: {"name"},
    w.apps.start: {"name"},
    w.apps.deploy: {"app_name", "app_deployment"},
    w.apps.get_deployment: {"app_name", "deployment_id"},
}
for fn, params in expected.items():
    missing = params - set(inspect.signature(fn).parameters)
    if missing:
        raise RuntimeError(f"databricks-sdk {sdk_version}: {fn.__qualname__} has no parameter(s) {sorted(missing)}. "
                           f"Run the job on serverless environment version 6 (databricks-sdk 0.122.0).")

# COMMAND ----------

# Deploy only what was tested: the Git folder must still be at the commit the pull task recorded.
repo = w.repos.get(repo_id=w.workspace.get_status(path=git_folder_path[len("/Workspace"):]).object_id)
head = (repo.head_commit_id or "").lower()
if commit and head != commit:
    raise RuntimeError(f"Git folder moved from {commit} to {head} after the pull task. Re-run the whole job.")
commit = commit or head

# The app reads its catalog from app.yaml; refuse to deploy one that points elsewhere.
with open(f"{git_folder_path}/app.yaml") as fh:
    m = re.search(r"name:\s*ACCRUALS_CATALOG\s*\n\s*value:\s*[\"']?([^\"'\s]+)", fh.read())
app_catalog = m.group(1) if m else None
if app_catalog != catalog:
    raise RuntimeError(f"app.yaml sets ACCRUALS_CATALOG={app_catalog!r} but this job deploys for catalog {catalog!r}. "
                       f"Fix app.yaml on main (or the catalog job parameter) and re-run.")

# COMMAND ----------

def v(enum):
    """Plain enum value for messages (FAILED rather than AppDeploymentState.FAILED)."""
    return getattr(enum, "value", enum)


def wait_for(describe, done, failed, timeout_s):
    """Poll describe() until done(x) or failed(x) is true; returns the last value."""
    deadline, delay = time.monotonic() + timeout_s, 5
    while True:
        x = describe()
        if done(x) or failed(x):
            return x
        if time.monotonic() > deadline:
            raise TimeoutError(f"Timed out after {timeout_s // 60} minutes.")
        time.sleep(delay)
        delay = min(delay + 5, 15)


app = w.apps.get(name=app_name)
compute = app.compute_status.state if app.compute_status else None
print(f"App {app_name}: compute {v(compute)}, app {v(app.app_status.state) if app.app_status else None}")

def compute_state(a):
    return a.compute_status.state if a.compute_status else None


if compute == ComputeState.STOPPING:
    compute = compute_state(wait_for(lambda: w.apps.get(name=app_name),
                                     lambda a: compute_state(a) == ComputeState.STOPPED,
                                     lambda a: compute_state(a) == ComputeState.ERROR, START_TIMEOUT_S))
if compute in (ComputeState.STOPPED, ComputeState.ERROR):
    print("Starting app compute...")
    w.apps.start(name=app_name)
    time.sleep(10)  # let the state leave STOPPED/ERROR before polling
if compute != ComputeState.ACTIVE:
    app = wait_for(lambda: w.apps.get(name=app_name),
                   lambda a: compute_state(a) == ComputeState.ACTIVE,
                   lambda a: compute_state(a) == ComputeState.ERROR,
                   START_TIMEOUT_S)
    if compute_state(app) != ComputeState.ACTIVE:
        raise RuntimeError(f"App compute did not start: {v(compute_state(app))}: {app.compute_status.message}")

# COMMAND ----------

dep = w.apps.deploy(app_name=app_name,
                    app_deployment=AppDeployment(source_code_path=git_folder_path,
                                                 mode=AppDeploymentMode.SNAPSHOT)).response
print(f"Deployment {dep.deployment_id} started from {git_folder_path} at commit {commit}")

dep = wait_for(lambda: w.apps.get_deployment(app_name=app_name, deployment_id=dep.deployment_id),
               lambda d: d.status and d.status.state == AppDeploymentState.SUCCEEDED,
               lambda d: d.status and d.status.state in (AppDeploymentState.FAILED, AppDeploymentState.CANCELLED),
               DEPLOY_TIMEOUT_S)
if dep.status.state != AppDeploymentState.SUCCEEDED:
    print(f"Deployment {dep.deployment_id} {v(dep.status.state)}: {dep.status.message}")
    raise RuntimeError(f"Deployment {v(dep.status.state)}: {dep.status.message}")
print(f"Deployment succeeded: {dep.status.message}")

app = wait_for(lambda: w.apps.get(name=app_name),
               lambda a: a.app_status and a.app_status.state == ApplicationState.RUNNING,
               lambda a: a.app_status and a.app_status.state == ApplicationState.CRASHED,
               RUNNING_TIMEOUT_S)
if app.app_status.state != ApplicationState.RUNNING:
    print(f"App {v(app.app_status.state)}: {app.app_status.message}")
    raise RuntimeError(f"App is {v(app.app_status.state)} after deployment: {app.app_status.message}. "
                       "Check the app's Logs tab.")

dbutils.jobs.taskValues.set(key="deployment_id", value=dep.deployment_id)
print(f"\nApp URL:          {app.url}")
print(f"Deployed commit:  {commit}")
print(f"Deployment id:    {dep.deployment_id}")
