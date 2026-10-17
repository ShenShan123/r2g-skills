"""Read the END of the logs, not the first pattern that matches.

The previous classifier matched `No rule to make target` first and stopped --
but that line is the DESIGNED frozen-layout fallback, which the log itself
annotates ("reading 6_final.odb/.gds only; no stage is rebuilt (RMD-P0-01)").
Classifying on it says only that the fallback ran, which it always does.
"""
import collections
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}

rows = []
for f in glob.glob(str(D / "n45_full/runs/*/result.json")):
    rd = Path(f).parent
    if "__".join(rd.name.split("__")[:-2]) not in dev:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("lvs_status") not in ("timeout", "lvs_failed"):
        continue
    if d.get("drc_status") in ("timeout", "drc_failed"):
        continue
    rows.append((d.get("cells") or 0, rd, d))
rows.sort()

lastline = collections.Counter()
for c, rd, d in rows:
    log = rd / "lvs_signoff.log"
    if not log.is_file():
        lastline["(文件不存在)"] += 1
        continue
    lines = [l.strip() for l in log.read_text(errors="replace").splitlines() if l.strip()]
    if not lines:
        lastline["(文件为空)"] += 1
        continue
    # normalise paths out so the same ending groups together
    last = lines[-1]
    for tok in ("/proj/", "C0", "C1", "C2", "C3", "R0", "R1"):
        if tok in last:
            last = last.split(tok)[0] + "<path>"
            break
    lastline[last[:90]] += 1

print("只有 LVS 无判决的 %d 份，按日志最后一行归类:\n" % len(rows))
for k, v in lastline.most_common(8):
    print("  %3d  %s" % (v, k))

print("\n完整尾部样本（取两份有日志的）:")
shown = 0
for c, rd, d in rows:
    log = rd / "lvs_signoff.log"
    if not log.is_file() or shown >= 2:
        continue
    lines = [l for l in log.read_text(errors="replace").splitlines() if l.strip()]
    print("\n  %d 单元  %s  (%d 行, lvs_seconds=%s)"
          % (c, rd.name[:42], len(lines), d.get("lvs_seconds")))
    for l in lines[-6:]:
        print("      %s" % l[:108])
    shown += 1
