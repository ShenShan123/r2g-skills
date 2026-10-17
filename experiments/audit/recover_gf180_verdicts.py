"""Read the 86 gf180 verdicts that exist on disk but not in result.json.

Provenance checked per design before counting any of it: the report must be
NEWER than the 6_final.gds it judges, and the directory must hold no repaired
layout -- these are re-runs of the baseline DRC against the untouched baseline
GDS, not post-repair results. C00024 is typical: GDS Oct 9 21:29, lyrdb Oct 11
20:25, no other artifact touched, while result.json still records the earlier
attempt's 7208 s timeout.

A DRC verdict alone does not make a design dual-clean, so LVS is reported
beside it.
"""
import collections
import glob
import json
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
ITEM = re.compile(r"<item>")
CAT = re.compile(r"<category>\s*'?([^'<]+)'?\s*</category>")

rows = []
for f in glob.glob(str(D / "gf180_full/runs/*/result.json")):
    rd = Path(f).parent
    if "__".join(rd.name.split("__")[:-2]) not in dev:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("drc_status") not in ("timeout", "drc_failed", None, "skipped"):
        continue
    gds = rd / "6_final.gds"
    rep = rd / "6_drc.lyrdb"
    if not (gds.is_file() and rep.is_file()):
        continue
    if rep.stat().st_mtime < gds.stat().st_mtime:
        continue
    txt = rep.read_text(errors="replace")
    n = len(ITEM.findall(txt))
    cats = collections.Counter(CAT.findall(txt))
    rows.append((rd.name, n, d.get("lvs_status"), cats))

print("可恢复判决的 gf180 设计: %d 份\n" % len(rows))
cl = [r for r in rows if r[1] == 0]
vi = [r for r in rows if r[1] > 0]
print("  DRC 干净 %d 份   有违例 %d 份" % (len(cl), len(vi)))
lvs = collections.Counter(r[2] for r in rows)
print("  这些设计的 LVS: %s" % dict(lvs))
both = [r for r in rows if r[1] == 0 and r[2] == "clean"]
print("\n  其中 DRC 干净 且 LVS 干净 = %d 份 <- 这些现在被记为失败，但其实是双清"
      % len(both))
rule = collections.Counter()
for _, _, _, c in vi:
    rule.update(c)
print("\n  有违例那些的规则分布 前 6:")
for k, v in rule.most_common(6):
    print("     %5d  %s" % (v, k))
Path(D / "_gf180_recovered.json").write_text(json.dumps(
    [{"design": a, "drc_violations": b, "lvs_status": c} for a, b, c, _ in rows],
    indent=1) + "\n")
