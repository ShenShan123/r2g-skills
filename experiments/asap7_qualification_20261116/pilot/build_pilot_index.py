#!/usr/bin/env python3
"""Assemble ONE ASAP7 pilot root on 216 from the run indexes of both servers, for make_spec_table.py.

  build_pilot_index.py OUT_ROOT INDEX.jsonl.gz [INDEX.jsonl.gz ...]

Per design the first source that has a synthesized size wins, in argument order (216 pilot, 203 pilot, 203
synth-only pass). OUT_ROOT/runs/<tag>/ gets: result.json (as recorded, plus "_source"), the top synth_stat.txt at
its original relative path (make_spec_table reads it for the top-module size), pins.json when the run's
6_final.def header gave a PINS count, and the PPL-0024 log line at its original path when the run hit one.
No layouts: this root carries sizes and pin counts only.
"""
import gzip
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
seen = {}
for idx in sys.argv[2:]:
    for line in gzip.open(idx, "rt"):
        rec = json.loads(line)
        name = rec["result"].get("design")
        if not name or name in seen:
            continue
        if not rec["result"].get("cells") and not rec.get("ppl0024"):
            continue
        seen[name] = rec
for name, rec in seen.items():
    rd = out / "runs" / rec["tag"]
    rd.mkdir(parents=True, exist_ok=True)
    res = dict(rec["result"], _source=rec["root"])
    (rd / "result.json").write_text(json.dumps(res, indent=1) + "\n")
    if rec.get("synth_stat"):
        p = rd / rec["synth_stat_relpath"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(rec["synth_stat"])
    if rec.get("pins_def") is not None:
        (rd / "pins.json").write_text(json.dumps({"pins": rec["pins_def"], "source": "pilot_def"}) + "\n")
    if rec.get("ppl0024"):
        p = rd / rec["ppl_log_relpath"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(rec["ppl0024"] + "\n")
print(len(seen), "designs ->", out)
