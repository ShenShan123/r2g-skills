#!/usr/bin/env python3
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    out_root_path,
)

INDEX = out_root_path("index.csv")


def main() -> None:
    rows = list(csv.DictReader(INDEX.open()))
    print(f"rows={len(rows)}")
    print(f"status_counts={dict(Counter(r['status'] for r in rows))}")

    failed = [r for r in rows if r["status"] != "success"]
    if failed:
        print("\nfailed_designs:")
        for r in failed:
            print(f"- {r['design']}: {r['status']}")

    families = {
        "iccad2015_": "iccad2015",
        "iccad2017_": "iccad2017",
        "iscas85_": "iscas85",
        "iscas89_": "iscas89",
    }
    print("\nfamily_counts:")
    for prefix, label in families.items():
        sub = [r for r in rows if r["design"].startswith(prefix)]
        if sub:
            print(f"- {label}: {dict(Counter(r['status'] for r in sub))}")


if __name__ == "__main__":
    main()
