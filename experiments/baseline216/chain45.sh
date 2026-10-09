#!/usr/bin/env bash
# nangate45 end to end: full flow -> backfill -> DRC + LVS signoff -> funnel.
#
# Period 2 ns. Round one of the P0 put the 80% crossing between 1.5 and 1.8 ns
# (12/16 = 75.0% at 1.5, 15/16 = 93.8% at 1.8), and 2 ns scores the same 93.8%
# as 1.8, so 2 ns costs nothing in met-rate and keeps continuity with the
# original calibration. Round two (60 random designs) was stopped after 18
# records -- two slow designs held all ten workers, since the queue expands as
# design x period and one design can occupy six slots -- and is kept only as a
# reference, not as evidence.
#
# 12 workers x 8 cores, matching the sky130hd campaign exactly so the two
# platforms' runtimes stay comparable. Side experiments may use fewer cores;
# a cross-platform run may not.
#
# PDN core floor 32 um, measured on nangate45's own ladder, not copied from
# sky130hd: 26 um fails with 25.84 of the 28.5 um it asks for, 32 um passes.
# The two platforms landing on the same number is a coincidence -- sky130hd
# needs 15.2 + 13.57, nangate45 needs 28.5 + 2.0.
set -uo pipefail

D=/proj/workarea/yangao
L=$D/chain45.log
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$L"; }

say "chain45: nangate45 full flow, $(wc -l < "$D/verilog_only.txt") designs @ 2ns"
python3 "$D/orfs_baseline.py" \
  --package "$D/rtl-certified-handoff-0002" \
  --select "$D/verilog_only.txt" \
  --out "$D/n45_full" --scratch "$D/n45_full_scratch" \
  --platforms nangate45 \
  --periods 'nangate45=2' \
  --workers 12 --cores-per-design 8 \
  --timeout 10800 --pdn-core-floor-um 32 \
  >> "$D/n45_full.driver.log" 2>&1
say "flow done"

say "backfill"
python3 "$D/backfill.py" --runs "$D/n45_full/runs" >> "$L" 2>&1 \
  || python3 - <<'PY' >> "$L" 2>&1
# backfill.py hardcodes the sky130hd path; fall back to an inline pass so a
# missing flag cannot cost the whole covariate set again.
import glob, json, os, re, tempfile
CELLS = re.compile(r"^\s*(\d+)\s+(\S+)\s+\d+\s+\S+\s+cells\s*$", re.M)
n = 0
for f in glob.glob("/proj/workarea/yangao/n45_full/runs/*/result.json"):
    d = json.load(open(f)); ch = False
    if d.get("cells") is None:
        for s in glob.glob(os.path.join(os.path.dirname(f), "reports", "*", "*",
                                        "base", "synth_stat.txt")):
            m = CELLS.search(open(s, errors="replace").read())
            if m:
                d["cells"] = int(m.group(1))
                try: d["area_um2"] = float(m.group(2))
                except ValueError: pass
                ch = True
            break
    if d.get("timing_path_constrained") is None and d.get("status") == "pass":
        for r in glob.glob(os.path.join(os.path.dirname(f), "reports", "*", "*",
                                        "base", "6_finish.rpt")):
            t = open(r, errors="replace").read()
            if re.search(r"worst slack max\s+INF", t):
                d["timing_path_constrained"] = False; ch = True
            elif d.get("worst_slack") is not None:
                d["timing_path_constrained"] = True; ch = True
            break
    if ch:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(f))
        with os.fdopen(fd, "w") as fh:
            json.dump(d, fh, indent=1); fh.write("\n")
        os.replace(tmp, f); n += 1
print(f"  inline backfill: {n} records")
PY

say "DRC + LVS signoff"
python3 "$D/signoff45.py" --workers 24 --timeout 7200 >> "$D/n45_signoff.log" 2>&1
say "signoff done"

python3 "$D/funnel.py" "$D/n45_full" >> "$D/N45_STATUS.md" 2>&1
say "chain45 all done"
