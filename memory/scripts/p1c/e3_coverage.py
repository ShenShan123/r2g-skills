#!/usr/bin/env python3
"""P1c E3: every transition of the 12 frozen TEHM stores maps to an exported unit (via the raw row it was captured
from), or is listed with the reason. Read-only. Usage: e3_coverage.py <code root> <export dir> <out.json>"""
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

E = Path("/data1/zhangdy/RTL/RTL_testbench/_r2g_eval")
code, exp, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(code / "memory"))
from tehm import neutral_export as nx  # noqa: E402
from tehm.adapters.r2g_evidence import _normalize_family  # noqa: E402

STORES = {"SL-hd": "sandbox-f/tehm/SL-hd", "SL-hs": "sandbox-f/tehm/SL-hs", "Mp-hd": "sandbox-f/tehm/Mp-hd",
          "Mp-hs": "sandbox-f/tehm/Mp-hs", "G-hd": "sandbox-f/tehm/G-hd", "G-hs": "sandbox-f/tehm/G-hs",
          "P-hd": "evaluation-h-ablation/no_llm_evidence-hd", "P-hs": "evaluation-h-ablation/no_llm_evidence-hs",
          "L-hd": "evaluation-h-ablation/no_component_trial_evidence-hd",
          "L-hs": "evaluation-h-ablation/no_component_trial_evidence-hs",
          "J-hd": "evaluation-i/stores/IJ-hd", "J-hs": "evaluation-i/stores/IJ-hs"}
RID = re.compile(r"^r2g:(.+):iter(\d+):([^:]+)$")
units = nx.read_export(exp)
by_ref = {}
for u in units:
    for s in u["provenance"]["sources"]:
        by_ref[(s["path"], s["line"])] = u["unit_id"]
# raw rows by session, per file: [(line, row)] sorted by iter (the old capture's step positions)
sess = collections.defaultdict(list)
files = [l.strip() for l in open(E / "p1c-export" / "all_fix_logs.txt") if l.strip()]
files += [str(f) for f in sorted((E / "g-trials").glob("*__*__*.json"))]
for f in files:
    p = Path(f)
    if not p.is_file():
        continue
    if p.suffix == ".json":
        rows = [(i, r) for i, r in enumerate(json.loads(p.read_text()), 1)]
    else:
        rows = [(i, json.loads(l)) for i, l in enumerate(p.read_text(errors="ignore").splitlines(), 1)
                if l.strip().startswith("{")]
    per = collections.defaultdict(list)
    for i, r in rows:
        if isinstance(r, dict) and r.get("fix_session_id"):
            per[str(r["fix_session_id"])].append((i, r))
    for s, lst in per.items():
        lst.sort(key=lambda x: int(x[1].get("iter", 0) or 0))
        sess[s].append((f, lst))

report = {}
for name, rel in STORES.items():
    c = sqlite3.connect("file:%s?mode=ro" % (E / rel / "tehm.sqlite"), uri=True)
    res = collections.Counter()
    unmapped = []
    for tid, prov in c.execute("SELECT transition_id, provenance_json FROM tehm_transitions"):
        rid = json.loads(prov or "{}").get("record_id") or ""
        m = RID.match(rid)
        hits = set()
        if m:
            s, k, fam = m.group(1), int(m.group(2)), m.group(3)
            for f, lst in sess.get(s, []):
                if k < len(lst) and _normalize_family(str(lst[k][1].get("strategy") or "none")).lower() == fam:
                    uid = by_ref.get((f, lst[k][0]))
                    if uid:
                        hits.add(uid)
        if len(hits) == 1:
            res["mapped_to_one_unit"] += 1
        elif hits:
            res["mapped_to_several_units"] += 1
            unmapped.append({"transition": tid, "record_id": rid, "units": sorted(hits)})
        else:
            res["no_unit"] += 1
            unmapped.append({"transition": tid, "record_id": rid, "units": []})
    report[name] = {"counts": dict(res), "exceptions": unmapped}
out.write_text(json.dumps(report, indent=1) + "\n")
for name, r in report.items():
    print("%-6s %s" % (name, r["counts"]))
ex = report["G-hs"]["exceptions"] + report["G-hd"]["exceptions"]
print("examples:", [(e["record_id"][:70], len(e["units"])) for e in ex[:8]])
