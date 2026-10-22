#!/usr/bin/env python3
"""List every nangate45 dev LVS judgement that has NO verdict, in both arms.

KLayout's comparer crashing leaves lvs_status=lvs_failed. The fallback
(lvs45_fallback.sh) is applied to exactly these rows -- wherever they come from:
the frozen baseline, the apparatus re-runs, the 6 h timeout re-run, every rung
of the antenna ladder, every knob/frontend treatment. One rule for both arms.

Writes "<tag> <run_dir> <out_dir>" lines for run_list.sh.
"""
import json
import re
import sys
from pathlib import Path

D = Path("/proj/workarea/yangao")
OUT = D / "lvs_netgen" / "fb"
dev = {"__".join(l.strip().split("__")[:3]) for l in (D / "split_dev.txt").read_text().splitlines() if l.strip()}


def rows(p: Path):
    try:
        r = json.loads(p.read_text())
    except Exception:
        return []
    return r if isinstance(r, list) else r.get("designs", [])


# (signoff json, runs dir its rows refer to, tag)
sources = [(D / "rerun_nangate45_20261018/signoff.json", D / "rerun_nangate45_20261018/runs", "baseline"),
           (D / "timeout_rerun.json", D / "_timeout_rerun_sel", "timeout6h")]
for b in ("ppl_fix_n45", "ppl_fix2_n45", "hier_fix_n45", "apparatus_fix_n45", "datafile_fix_n45"):
    for so in (D / b).glob("signoff*.json"):
        sources.append((so, D / b / "runs", b))
for pat, tag in (("r2g_repair_n45/r[123]/*/signoff.json", "antenna"),
                 ("knob_ab/*/*/signoff.json", "knob"),
                 ("sv_frontend_spec*/*/signoff.json", "frontend")):
    for so in D.glob(pat):
        sources.append((so, so.parent / "runs", tag + ":" + str(so.parent.relative_to(D)).replace("/", ":")))

seen, lines = set(), []
for so, runs, tag in sources:
    for r in rows(so):
        if r.get("lvs_status") != "lvs_failed":
            continue
        name = r.get("design", "")
        short = "__".join(re.sub(r"^\d+__", "", name).split("__")[:3])
        if short not in dev:
            continue
        rd = runs / name
        if not rd.exists():
            print("缺 run dir:", rd, file=sys.stderr)
            continue
        key = str(rd.resolve())
        if key in seen:
            continue
        seen.add(key)
        out = OUT / tag.split(":")[0] / (tag.replace(":", "_") + "__" + re.sub(r"^\d+__", "", name))
        lines.append("%s %s %s" % (tag.split(":")[0], rd, out))
sys.stdout.write("".join(l + "\n" for l in lines))
print(len(lines), "rows", file=sys.stderr)
