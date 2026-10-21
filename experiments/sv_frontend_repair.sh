#!/usr/bin/env bash
# Frontend repair: designs whose RTL the default yosys parser rejects.
#
# 21 nangate45 dev designs die in 1_1_yosys_canonicalize on SystemVerilog the
# built-in read_verilog does not accept (`syntax error, unexpected TOK_CONSTVAL /
# TOK_INITIAL / TOK_REG / '['`, `Failed to detect width for parameter`). ORFS has
# a slang frontend switch (SYNTH_HDL_FRONTEND=slang), but the image's yosys 0.64
# cannot load the only slang plugin present -- oss-cad-suite's, built for yosys
# 0.51 (undefined symbol _ZN5Yosys5RTLIL2ID4ARGSE).
#
# So slang is used as a PRE-PROCESSOR only: oss-cad-suite's own yosys reads the
# original sources with read_slang and writes plain Verilog, and the ORFS flow
# then runs completely unchanged from that file -- its yosys 0.64 synthesis,
# ABC, placement, routing, and the same sign-off. Nothing downstream of "reading
# the RTL" differs from the baseline.
#
# Attribution: this is a REPAIR (repair arm only, diagnosis-driven: applied only
# to a design whose default parse failed), reported separately as a frontend
# repair rather than a physical-design one.
#
# Usage: sv_frontend_repair.sh <design_short_name> <out_root>
# Leaves <out_root>/<name>/runs/<run>/ shaped for signoff45.py.
set -uo pipefail
D=/proj/workarea/yangao
NM=$1
OUTROOT=${2:-$D/sv_frontend_n45}
IMAGE=localhost/r2g-orfs:26Q3
PKG=$D/rtl-certified-handoff-0002
RUN=${NM}__nangate45__p2
SRC=$D/rerun_nangate45_20261018/runs/$RUN
W=$OUTROOT/$NM
CORES=${CORES:-8}
rm -rf "$W"; mkdir -p "$W/work/_sv" "$W/runs/$RUN"
cp "$SRC/config.mk" "$SRC/constraint.sdc" "$W/work/"
TOP=$(grep -oP '(?<=^export DESIGN_NAME = ).*' "$W/work/config.mk" | tr -d ' ')

# file list / include dirs / defines straight from the package metadata, the
# same source orfs_spec.py builds the baseline config from
python3 - "$PKG/certified/$NM/DESIGN.json" "$PKG/certified/$NM" "$W/work/_sv/args" <<'PY'
import json, sys
from pathlib import Path
meta = json.load(open(sys.argv[1])); dd = Path(sys.argv[2])
args = []
for i in meta.get("include_dirs") or []:
    args.append("-I" + str((dd / i).resolve()))
for d in meta.get("defines") or []:
    args.append("-D" + d.split("=", 1)[0] + ("=" + d.split("=", 1)[1] if "=" in d else ""))
seen = set()
for f in meta.get("source_files") or []:
    p = str((dd / f["file"]).resolve())
    if p not in seen:
        seen.add(p); args.append(p)
open(sys.argv[3], "w").write("\n".join(args) + "\n")
PY

# slang reads everything at once, exactly as ORFS's own slang path does
# (synth_preamble.tcl: -D SYNTHESIS --keep-hierarchy --compat=vcs
#  --ignore-assertions --top <top>)
podman run --rm -v "$W/work":/work:Z -v "$PKG":"$PKG":ro,Z "$IMAGE" bash -lc "
  set -e
  mapfile -t A < /work/_sv/args
  /r2g_toolchain/oss-cad-suite/bin/yosys -q -l /work/_sv/slang.log -p \"
    plugin -i slang;
    read_slang -D SYNTHESIS --keep-hierarchy --compat=vcs --ignore-assertions --top $TOP \${A[*]};
    hierarchy -top $TOP;
    proc; opt_clean;
    write_verilog -noattr /work/_sv/${TOP}_slang.v\"
" > "$W/slang.out" 2>&1
if [ ! -s "$W/work/_sv/${TOP}_slang.v" ]; then
  echo "slang_failed: $(grep -m1 -iE 'error' "$W/work/_sv/slang.log" "$W/slang.out" 2>/dev/null | cut -c1-160)"
  exit 3
fi

# the ONLY config change: the flow reads the pre-processed file instead
python3 - "$W/work/config.mk" "/work/_sv/${TOP}_slang.v" <<'PY'
import re, sys
p, v = sys.argv[1], sys.argv[2]
t = open(p).read()
t = re.sub(r"(?m)^export VERILOG_FILES\s*=.*$", "export VERILOG_FILES = " + v, t)
t = re.sub(r"(?m)^export VERILOG_INCLUDE_DIRS\s*=.*\n", "", t)
t = re.sub(r"(?m)^export VERILOG_DEFINES\s*=.*\n", "", t)
open(p, "w").write(t)
PY

podman run --rm --name "svf_${NM:0:40}" -v "$W/work":/work:Z -v "$PKG":"$PKG":ro,Z \
  -e NUM_CORES="$CORES" -e OMP_NUM_THREADS="$CORES" "$IMAGE" bash -lc \
  'cd $ORFS_ROOT/flow && make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work' > "$W/flow.log" 2>&1
rc=$?
R=$W/work/results/nangate45/$TOP/base
if [ $rc -ne 0 ] || [ ! -f "$R/6_final.gds" ]; then
  echo "flow_rc_$rc: $(grep -oE '\[ERROR [A-Z]+-[0-9]+\][^"]{0,80}|ERROR: .{0,80}' "$W/flow.log" | tail -1)"
  exit 4
fi
for f in 6_final.def 6_final.gds 6_final.odb 6_final.v 6_final.sdc 6_final.spef 5_route.odb; do
  [ -f "$R/$f" ] && cp "$R/$f" "$W/runs/$RUN/"
done
cp "$W/work/config.mk" "$W/work/constraint.sdc" "$W/runs/$RUN/"
python3 - "$SRC/result.json" "$W/runs/$RUN/result.json" "$W/work" <<'PY'
import glob, json, sys
src, dst, w = sys.argv[1:4]
d = json.load(open(src))
for f in glob.glob(w + "/logs/*/*/base/6_report.json"):
    m = json.load(open(f)); d["cells"] = m.get("finish__design__instance__count"); break
d["status"] = "pass"; d["frontend"] = "slang-preprocess(oss-cad-suite yosys 0.51)"
for k in ("failure_class", "drc_status", "lvs_status", "signoff_seconds"): d.pop(k, None)
json.dump(d, open(dst, "w"), indent=1)
PY
python3 -I "$D/signoff45.py" --runs "$W/runs" --projroot "$W/judge_proj" \
  --out "$W/signoff.json" --workers 1 --force > "$W/judge.log" 2>&1
python3 - "$W/signoff.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1])); r = (r if isinstance(r, list) else r["designs"])[0]
print("judged drc=%s lvs=%s violations=%s" % (r.get("drc_status"), r.get("lvs_status"), r.get("drc_violations")))
PY
