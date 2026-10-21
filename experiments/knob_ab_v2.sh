#!/usr/bin/env bash
# Treatment arm of a configuration-knob A/B on one design.
#
# The control arm is the frozen baseline's own verdict for the design: same
# spec, same image, same sign-off. The treatment is that design's baseline
# config.mk plus the knob lines given here, run through the full flow and judged
# by signoff45.py -- the same checker -- so the only difference between the arms
# is the knob. Routing has been shown independent of NUM_CORES (byte-identical
# COMPONENTS and NETS at 4 vs 8 cores), so CORES may be lowered without
# confounding the comparison.
#
# Usage: knob_ab.sh <run_name> <tag> 'export VAR = value' ['export ...' ...]
#
# MAKE_ARGS='VAR="value" ...' passes variables on the make COMMAND LINE. Needed
# for anything the platform config assigns with `=` after the design config is
# included (DONT_USE_CELLS, TECH_LEF, ...): there a config.mk line is silently
# overridden, while a command-line variable beats every makefile assignment.
set -uo pipefail
D=/proj/workarea/yangao
RUN=$1; TAG=$2; shift 2
SRC=$D/rerun_nangate45_20261018/runs/$RUN
W=$D/knob_ab/$TAG/$RUN
IMAGE=localhost/r2g-orfs:26Q3
CORES=${CORES:-4}
rm -rf "$W"; mkdir -p "$W/work" "$W/runs/$RUN"
cp "$SRC/config.mk" "$SRC/constraint.sdc" "$W/work/"
# EXTRA_FILES="<host path> ...": copied into the work dir (seen as /work/<name>)
for f in ${EXTRA_FILES:-}; do cp "$f" "$W/work/"; done
# SPEC_TABLE=<spec json>: apply this design's frozen period and die exactly as
# orfs_spec.py does for a design WITH an entry -- `set _per` in the SDC (the
# uncertainty and I/O delays derive from it), DIE_AREA/CORE_AREA in config.mk,
# and no CORE_UTILIZATION. Used for designs the baseline ran on the platform
# default because the pilot never sized them; refuses if the entry is missing.
if [ -n "${SPEC_TABLE:-}" ]; then
  python3 - "$SPEC_TABLE" "$RUN" "$W/work/config.mk" "$W/work/constraint.sdc" <<'PY' || { echo "result no_spec_entry"; exit 6; }
import json, re, sys
tbl, run, cfg, sdc = sys.argv[1:5]
short = "__".join(run.split("__")[:3])
e = json.load(open(tbl)).get(short)
if not e:
    sys.exit(1)
c = open(cfg).read()
c = re.sub(r"(?m)^export (CORE_UTILIZATION|DIE_AREA|CORE_AREA)\s*=.*\n", "", c)
d, i = e["die_side_um"], e["core_inset_um"]
c += "export DIE_AREA  = 0 0 %g %g\nexport CORE_AREA = %g %g %g %g\n" % (d, d, i, i, d - i, d - i)
open(cfg, "w").write(c)
t = open(sdc).read()
t2 = re.sub(r"(?m)^set _per\s+\S+", "set _per %s" % e["period_ns"], t)
assert t2 != t or ("set _per %s" % e["period_ns"]) in t
open(sdc, "w").write(t2)
print("readback SPEC: period %s ns  die %g  core %g  size_source %s"
      % (e["period_ns"], d, e["core_side_um"], e.get("size_source")))
PY
fi
for line in "$@"; do
  var=$(echo "$line" | awk '{print $2}')
  sed -i "/^export $var[[:space:]]*=/d" "$W/work/config.mk"
  echo "$line" >> "$W/work/config.mk"
done
TOP=$(grep -oP '(?<=^export DESIGN_NAME = ).*' "$W/work/config.mk" | tr -d ' ')
# Optional router-model patch: TECH_LEF_PATCH=<file> is bind-mounted over the
# platform's own tech LEF (setting TECH_LEF in config.mk is silently overridden
# by the platform config's `=`), and TECH_LEF_EXPECT=<grep pattern> must be
# found in the mounted file from INSIDE the container or nothing runs.
LEFPATH=/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/nangate45/lef/NangateOpenCellLibrary.tech.lef
EXTRA=()
if [ -n "${TECH_LEF_PATCH:-}" ]; then
  cp "$TECH_LEF_PATCH" "$W/work/tech_patch.lef"
  got=$(podman run --rm -v "$W/work/tech_patch.lef":"$LEFPATH":ro,Z "$IMAGE" \
        bash -lc "grep -c -E '${TECH_LEF_EXPECT:-.}' $LEFPATH" 2>/dev/null)
  [ "${got:-0}" -gt 0 ] || { echo "result lef_patch_not_effective"; exit 5; }
  echo "readback TECH_LEF_PATCH: '${TECH_LEF_EXPECT:-.}' x$got inside the container"
  EXTRA=(-v "$W/work/tech_patch.lef":"$LEFPATH":ro,Z)
fi
podman run --rm --name "kab_${TAG:0:12}_${RUN:0:30}" -v "$W/work":/work:Z "${EXTRA[@]}" \
  -v "$D/rtl-certified-handoff-0002":"$D/rtl-certified-handoff-0002":ro,Z \
  -e NUM_CORES="$CORES" -e OMP_NUM_THREADS="$CORES" "$IMAGE" bash -lc \
  "cd \$ORFS_ROOT/flow && make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work ${MAKE_ARGS:-}" > "$W/flow.log" 2>&1
rc=$?
R=$W/work/results/nangate45/$TOP/base
# read-back: the knob must be in effect in the stage that consumes it
for line in "$@"; do
  var=$(echo "$line" | awk '{print $2}')
  echo "readback $var: $(grep -h -m1 -E "$var" "$W"/work/logs/nangate45/$TOP/base/*.log 2>/dev/null | head -1 | cut -c1-100)"
done
if [ $rc -ne 0 ] || [ ! -f "$R/6_final.gds" ]; then
  echo "result flow_rc_$rc $(grep -oE '\[ERROR [A-Z]+-[0-9]+\][^"]{0,90}' "$W/flow.log" | tail -1)"
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
    m = json.load(open(f)); d["cells"] = m.get("finish__design__instance__count")
    d["ws"] = m.get("finish__timing__setup__ws"); break
d["status"] = "pass"
for k in ("failure_class", "drc_status", "lvs_status", "signoff_seconds", "message"): d.pop(k, None)
json.dump(d, open(dst, "w"), indent=1)
PY
python3 -I "$D/signoff45.py" --runs "$W/runs" --projroot "$W/judge_proj" \
  --out "$W/signoff.json" --workers 1 --force > "$W/judge.log" 2>&1
python3 - "$W/signoff.json" "$W/runs/$RUN/result.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1])); r = (r if isinstance(r, list) else r["designs"])[0]
ws = json.load(open(sys.argv[2])).get("ws")
print("result drc=%s lvs=%s violations=%s ws=%s" % (r.get("drc_status"), r.get("lvs_status"),
                                                     r.get("drc_violations"), ws))
PY
