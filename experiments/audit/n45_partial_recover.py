"""Can the 67 lost nangate45 verdicts be recovered from what already exists?

The ANTENNA block is the LAST section of FreePDK45.lydrc and all 67 stall
inside it, which means every other rule -- about 370 of them -- already ran. If
the run left a report behind, the non-antenna verdict is already on disk and
costs nothing to read.

What this CANNOT do is call such a design clean: a design that passed every
non-antenna rule but never finished the antenna check has an UNKNOWN antenna
result, and unknown is not a pass. The recovered verdict is
`clean_except_antenna`, a third value, never folded into `clean`.
"""
import collections
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}

have = collections.Counter()
rows = []
for f in glob.glob(str(D / "n45_full/runs/*/result.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    rd = Path(f).parent
    if "__".join(rd.name.split("__")[:-2]) not in dev:
        continue
    if d.get("drc_status") not in ("timeout", "drc_failed"):
        continue
    arts = {p.name: p.stat().st_size for p in rd.iterdir()
            if p.suffix in (".lyrdb", ".rpt", ".xml") or "drc" in p.name.lower()}
    have[tuple(sorted(arts))] += 1
    rows.append((rd.name, arts))

print("67 份里各自留下了什么产物:")
for k, v in have.most_common(6):
    print("   %3d 份: %s" % (v, ", ".join(k) if k else "(什么都没有)"))
print()
for name, arts in rows[:3]:
    print("  %s" % name[:56])
    for a, s in sorted(arts.items()):
        print("       %-28s %d bytes" % (a, s))
