#!/usr/bin/env python3
"""P1c E2 dry run: export every raw fix log + component-trial file into a neutral export (read-only on the raw
sources). Usage: export_all.py <code root> <out dir>. Not yet the SOURCE freeze (E5/E6 come later)."""
import collections
import json
import subprocess
import sys
from pathlib import Path

E = Path("/data1/zhangdy/RTL/RTL_testbench/_r2g_eval")
code, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(code / "memory"))
from tehm import neutral_export as nx  # noqa: E402

LANE = {"hd": "sky130hd", "hs": "sky130hs"}
sources, skipped = [], collections.Counter()
for line in open(E / "p1c-export" / "all_fix_logs.txt"):
    p = Path(line.strip())
    if not p.is_file():
        continue
    if "/tests/fixtures/" in str(p):                     # test fixtures are not experience
        skipped["test_fixture"] += 1
        continue
    s = nx.read_fix_log_source(p)
    if not s["design"] or not s["platform"]:
        skipped["no_design_or_platform"] += 1
        continue
    sources.append(s)
for f in sorted((E / "g-trials").glob("*__*__*.json")):
    sit_key, design, _variant = f.stem.split("__")
    sources.append(nx.read_trial_source(f, design=design, platform=LANE[sit_key.split("_")[1]]))
units, log = nx.build_units(sources, resolver=nx.R2GPreActionResolver(E))
commit = subprocess.run(["git", "-C", str(code), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
manifest = nx.write_export(units, out, dedup_log=log, meta={"code_commit": commit, "sources": len(sources),
                                                            "sources_skipped": dict(skipped), "status": "dry-run"})
print("sources %d (skipped %s), units %d, copies merged %d, conflicts: annotation %d, measured %d" % (
    len(sources), dict(skipped), log["units"], log["copies_merged"], log["conflicts_annotation"],
    log["conflicts_measured"]))
print("export sha256", manifest["export_sha256"])
print("context conflicts %d; situation fields UNKNOWN: %s" % (log["conflicts_context"], log["situation_unknown_fields"]))
# semantic validation: unified vs every recorded annotation, per field
mis, tot = collections.Counter(), collections.Counter()
for u in units:
    if not u["source_eligible"]:
        continue
    us = u["raw_row"]["situation"]
    for rv in u["raw_variants"]:
        rs = rv.get("situation") if isinstance(rv.get("situation"), dict) else None
        if rs is None:
            continue
        for f, val in us.items():
            if f == "v" or (f == "perimeter_band" and "perimeter_band" not in rs) or val == nx.UNKNOWN:
                continue
            tot[f] += 1
            if rs.get(f) != val:
                mis[f] += 1
print("validation (unified vs recorded, known fields): " + ", ".join("%s %d/%d" % (f, mis[f], tot[f]) for f in sorted(tot)))
fields = collections.Counter(tuple(u["conflict"]["fields"]) for u in units if u["conflict"])
print("conflict fields:", fields.most_common(8))
print("origin x rolled_back:", collections.Counter((u["action"]["origin"], u["rollback"]["rolled_back"]) for u in units).most_common())
pos = [u for u in units if u["outcome"]["verified"] and u["outcome"]["canonical"] in ("PASS", "PARTIAL")]
print("verified PASS/PARTIAL units %d; recovery evidence: %s" % (len(pos), collections.Counter(
    "+".join(sorted({e["basis"] for e in u["rollback"]["recovery_evidence"]})) or ("marked" if u["rollback"]["rolled_back"] != "UNKNOWN" else "none")
    for u in pos).most_common()))
