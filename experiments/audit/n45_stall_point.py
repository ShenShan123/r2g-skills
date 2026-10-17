"""Where do nangate45's ungradable designs stop?

If they all stop in the same check, that check -- not the design, not the size
alone -- is what costs 67 verdicts, and it becomes a contract decision rather
than an engineering one.
"""
import collections
import glob
import json
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
LINE = re.compile(r'in:\s+(\S+\.lydrc):(\d+)')
SECTION = re.compile(r"^([A-Z][A-Za-z0-9 _-]{3,40})$")

stall = collections.Counter()
sect = collections.Counter()
n = 0
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
    log = rd / "drc_signoff.log"
    if not log.is_file():
        stall["(无日志)"] += 1
        n += 1
        continue
    txt = log.read_text(errors="replace")
    ms = LINE.findall(txt)
    last_sect = "?"
    for line in txt.splitlines():
        if SECTION.match(line.strip()) and "check" in line.lower():
            last_sect = line.strip()
    stall[ms[-1][1] if ms else "?"] += 1
    sect[last_sect] += 1
    n += 1

print("nangate45 dev 无 DRC 判决 %d 份\n" % n)
print("停在 FreePDK45.lydrc 的哪一行:")
for k, v in stall.most_common(8):
    print("   %4d 份  行 %s" % (v, k))
print("\n最后进入的检查段:")
for k, v in sect.most_common(6):
    print("   %4d 份  %s" % (v, k))
