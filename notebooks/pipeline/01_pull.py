# Databricks notebook source
# MAGIC %md
# MAGIC # 01 Pull
# MAGIC Updates the deployment Git folder to the latest commit on the branch (or to a tag) and records the
# MAGIC commit hash as the task value `commit` for the test and deploy tasks.
# MAGIC
# MAGIC | Parameter | Meaning |
# MAGIC |---|---|
# MAGIC | `git_folder_path` | Deployment Git folder, e.g. `/Workspace/Shared/citi_accruals` |
# MAGIC | `branch` | Branch to pull (default `main`) |
# MAGIC | `tag` | Optional. Check out this tag instead of the branch (rollback) |
# MAGIC | `commit` | Optional. Expected commit hash; the run fails unless the Git folder ends up exactly there |
# MAGIC
# MAGIC Git folders can only be moved to a branch or a tag (`ReposAPI.update` has no commit argument), so a
# MAGIC rollback names a tag or branch that points at the old commit and passes the hash in `commit` as a check.

# COMMAND ----------

import inspect

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ObjectType
from databricks.sdk.version import __version__ as sdk_version

dbutils.widgets.text("git_folder_path", "")
dbutils.widgets.text("branch", "main")
dbutils.widgets.text("tag", "")
dbutils.widgets.text("commit", "")

git_folder_path = dbutils.widgets.get("git_folder_path").strip().rstrip("/")
branch = dbutils.widgets.get("branch").strip()
tag = dbutils.widgets.get("tag").strip()
commit = dbutils.widgets.get("commit").strip().lower()

if not git_folder_path:
    raise ValueError("Set the git_folder_path job parameter, e.g. /Workspace/Shared/citi_accruals")
if not tag and not branch:
    raise ValueError("Set branch (normally main) or tag.")
if commit and len(commit) < 7:
    raise ValueError("commit must be at least 7 characters of the hash.")

# COMMAND ----------

# Calls below were checked against databricks-sdk 0.49.0, 0.67.0 and 0.122.0 (serverless environments 3-6).
# Confirm the installed SDK still has them before doing anything.
w = WorkspaceClient()
print(f"databricks-sdk {sdk_version}")
expected = {
    w.workspace.get_status: {"path"},
    w.repos.get: {"repo_id"},
    w.repos.update: {"repo_id", "branch", "tag"},
}
for fn, params in expected.items():
    missing = params - set(inspect.signature(fn).parameters)
    if missing:
        raise RuntimeError(f"databricks-sdk {sdk_version}: {fn.__qualname__} has no parameter(s) {sorted(missing)}. "
                           f"Run the job on serverless environment version 6 (databricks-sdk 0.122.0).")

# COMMAND ----------

# The Workspace API takes paths without the /Workspace prefix; file access and Apps use the prefixed form.
api_path = git_folder_path[len("/Workspace"):] if git_folder_path.startswith("/Workspace/") else git_folder_path
status = w.workspace.get_status(path=api_path)
if status.object_type != ObjectType.REPO:
    raise RuntimeError(f"{git_folder_path} is not a Git folder (it is a {getattr(status.object_type, 'value', None)}).")
repo_id = status.object_id
repo = w.repos.get(repo_id=repo_id)
if not (repo.path or "").rstrip("/").endswith(api_path):
    raise RuntimeError(f"Git folder id {repo_id} resolved to {repo.path}, not {git_folder_path}.")
print(f"Git folder {git_folder_path} (id {repo_id}) was on {repo.branch or 'detached HEAD'} at {repo.head_commit_id}")

# COMMAND ----------

if tag:
    w.repos.update(repo_id=repo_id, tag=tag)
    ref = f"tag {tag}"
else:
    w.repos.update(repo_id=repo_id, branch=branch)
    ref = f"branch {branch}"

repo = w.repos.get(repo_id=repo_id)
head = (repo.head_commit_id or "").lower()
if not head:
    raise RuntimeError("The Git folder reports no head commit after the update.")
if commit and not head.startswith(commit):
    raise RuntimeError(f"Expected commit {commit} but {ref} is at {head}. Point a tag or branch at {commit} "
                       f"and pass it as tag/branch (see DEPLOY.md, Rollback).")

dbutils.jobs.taskValues.set(key="commit", value=head)
dbutils.jobs.taskValues.set(key="ref", value=ref)
print(f"Git folder now on {ref} at commit {head}")
