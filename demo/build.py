"""Assemble the single-file demo.

    python -m demo.build_data && python -m demo.build

Injects the base64 logo, demo/out/demo_data.json and design/nyl.css into demo/template.html and writes
demo/out/Citi_Accruals_Demo.html. Fails if the result contains anything that looks like bank details.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from demo.build_data import BANK_PATTERNS  # noqa: E402

TEMPLATE = os.path.join(ROOT, "demo", "template.html")
CSS = os.path.join(ROOT, "design", "nyl.css")
LOGO = os.path.join(ROOT, "assets", "nyl_logo.png")
OUT_DIR = os.environ.get("ACCRUALS_DEMO_OUT_DIR", os.path.join(ROOT, "demo", "out"))
DATA = os.environ.get("ACCRUALS_DEMO_DATA", os.path.join(OUT_DIR, "demo_data.json"))
OUT = os.environ.get("ACCRUALS_DEMO_HTML", os.path.join(OUT_DIR, "Citi_Accruals_Demo.html"))


def build() -> tuple[str, str]:
    with open(TEMPLATE, encoding="utf-8") as fh:
        html = fh.read()
    with open(CSS, encoding="utf-8") as fh:
        css = fh.read()
    with open(LOGO, "rb") as fh:
        logo = "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")
    with open(DATA, encoding="utf-8") as fh:
        data = json.dumps(json.load(fh), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    for token in ("__CSS__", "__LOGO__", "__DATA__"):
        if token not in html:
            raise ValueError(f"{TEMPLATE} has no {token} placeholder")
    if "</style" in css.lower():
        raise ValueError("design/nyl.css must not contain </style")
    # CSS and logo first; the data goes in last so nothing inside it is ever treated as a placeholder.
    return html.replace("__CSS__", css).replace("__LOGO__", logo).replace("__DATA__", data), logo


def main() -> int:
    html, logo = build()
    text = html.replace(logo, "")  # base64 image data can look like an account number
    found = [p for p in BANK_PATTERNS if re.search(p, text)]
    if found:
        print("Demo NOT written: it contains text matching", found)
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Wrote {OUT} ({len(html) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
