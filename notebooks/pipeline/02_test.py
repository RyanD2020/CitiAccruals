# Databricks notebook source
# MAGIC %md
# MAGIC # 02 Test
# MAGIC Installs `requirements.txt` and runs `tests/test_core.py` against the test documents with the SQLite
# MAGIC backend, then asserts the regression baseline. Fails the task on any difference.
# MAGIC
# MAGIC Only counts and totals are printed. Invoice lines, issue text and document content are never shown,
# MAGIC and if the test raises, only the exception type and code location are reported.
# MAGIC
# MAGIC | Parameter | Meaning |
# MAGIC |---|---|
# MAGIC | `test_zip` | `/Volumes/<catalog>/citi_accruals/documents/test_fixtures/Citi_Accruals_Documents.zip` |

# COMMAND ----------

# MAGIC %pip install -q -r ../../requirements.txt

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import sys
import traceback

dbutils.widgets.text("test_zip", "")
test_zip = dbutils.widgets.get("test_zip").strip()
if not test_zip or not os.path.exists(test_zip):
    raise FileNotFoundError(f"Test zip not found: {test_zip!r}. Set the test_zip job parameter.")

# This notebook lives in <repo>/notebooks/pipeline and runs with that folder as its working directory.
repo_root = os.path.abspath(os.path.join(os.getcwd(), "..", ".."))
if not os.path.exists(os.path.join(repo_root, "tests", "test_core.py")):
    raise RuntimeError(f"tests/test_core.py not found under {repo_root}")
sys.path.insert(0, repo_root)

commit = dbutils.jobs.taskValues.get(taskKey="pull", key="commit", default="", debugValue="")
print(f"Testing {repo_root} at commit {commit or '(interactive run, no pull task)'}")

# COMMAND ----------

from tests import test_core

try:
    metrics = test_core.run(test_zip, verbose=False)
except Exception as exc:
    frames = traceback.extract_tb(exc.__traceback__)
    where = next((f for f in reversed(frames) if f.filename.startswith(repo_root)), frames[-1])
    raise RuntimeError(
        f"Regression test raised {type(exc).__name__} at {os.path.relpath(where.filename, repo_root)}:{where.lineno}. "
        "The message is not shown because it can contain document content; run tests/test_core.py "
        "interactively to see it."
    ) from None

# COMMAND ----------

problems = test_core.check_baseline(metrics)
width = max(len(k) for k in test_core.BASELINE)
print(f"{'check':<{width}}  {'expected':>12}  {'actual':>12}")
for key, want in test_core.BASELINE.items():
    got = metrics.get(key)
    flag = "" if not any(p.startswith(key + ":") for p in problems) else "   <-- MISMATCH"
    print(f"{key:<{width}}  {str(want):>12}  {str(got):>12}{flag}")
print(f"\nInfo: register sync new/changed = {metrics.get('register_diff_new')}/{metrics.get('register_diff_changed')}, "
      f"outstanding after reverse/re-post = {metrics.get('outstanding_after_repost')}")

if problems:
    raise AssertionError("Regression baseline does not hold:\n  " + "\n  ".join(problems))
print("\nBaseline holds.")
