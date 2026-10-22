#!/usr/bin/env python3
"""Apply an item-by-item restoration review to an export -> the post-recovery export.
Usage: post_recovery.py <code root> <export dir> <review.json> <out dir>"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

code, exp, review, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
sys.path.insert(0, str(code / "memory"))
from tehm import neutral_export as nx  # noqa: E402

units = nx.read_export(exp)
manifest_in = json.loads((exp / "source_manifest.json").read_text())
reviews = json.loads(review.read_text())["reviews"]
counts = nx.apply_restorations(units, reviews)
commit = subprocess.run(["git", "-C", str(code), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
m = nx.write_export(units, out, dedup_log=manifest_in["dedup_log"], meta={
    "code_commit": commit, "base_export_sha256": manifest_in["export_sha256"],
    "restoration_review": str(review), "restoration_review_sha256": hashlib.sha256(review.read_bytes()).hexdigest(),
    "restorations": counts, "status": "post-recovery (not frozen)"})
print("units %d, restorations %s, export sha256 %s" % (len(units), counts, m["export_sha256"]))
