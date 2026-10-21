# Databricks notebook source
# MAGIC %md
# MAGIC # Build the HTML demo on Databricks
# MAGIC Use this when the demo can't be built locally (for example while PyPI is blocked). Installs
# MAGIC `requirements.txt`, runs the regression test, then `demo/build_data.py` and `demo/build.py`, and writes
# MAGIC the demo to a volume folder. Only counts, totals and file paths are printed.
# MAGIC
# MAGIC | Parameter | Meaning |
# MAGIC |---|---|
# MAGIC | `test_zip` | `/Volumes/<catalog>/citi_accruals/documents/test_fixtures/Citi_Accruals_Documents.zip` |
# MAGIC | `output_dir` | Where to write the demo, e.g. `/Volumes/<catalog>/citi_accruals/documents/demo_out` |

# COMMAND ----------

# MAGIC %pip install -q -r ../../requirements.txt

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import subprocess
import sys

dbutils.widgets.text("test_zip", "")
dbutils.widgets.text("output_dir", "")
test_zip = dbutils.widgets.get("test_zip").strip()
output_dir = dbutils.widgets.get("output_dir").strip().rstrip("/")
if not os.path.exists(test_zip):
    raise FileNotFoundError(f"Test zip not found: {test_zip!r}")
if not output_dir.startswith("/Volumes/"):
    raise ValueError("output_dir must be a volume folder, so nothing is written into the Git folder.")
os.makedirs(output_dir, exist_ok=True)

# This notebook lives in <repo>/notebooks/checks.
repo_root = os.path.abspath(os.path.join(os.getcwd(), "..", ".."))
env = dict(os.environ, ACCRUALS_DEMO_ZIP=test_zip, ACCRUALS_DEMO_OUT_DIR=output_dir)


def run(*args):
    """Run a step from the repo root; its output is counts and totals only."""
    r = subprocess.run([sys.executable, *args], cwd=repo_root, env=env, capture_output=True, text=True)
    return r.returncode, r.stdout


# The regression test prints document detail in verbose mode, so run it quietly and print only the baseline.
sys.path.insert(0, repo_root)
from tests import test_core

problems = test_core.check_baseline(test_core.run(test_zip, verbose=False))
print("Regression baseline:", "holds" if not problems else "FAILED")
for p in problems:
    print("  " + p)

code, out = run("-m", "demo.build_data")
print(out)
if code == 0:
    code, out = run("-m", "demo.build")
    print(out)
if problems or code:
    raise RuntimeError("Demo not built; see the differences above.")
print(f"Download {output_dir}/Citi_Accruals_Demo.html from Catalog Explorer.")
