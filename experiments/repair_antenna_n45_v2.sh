#!/usr/bin/env bash
# The r2g repair arm on nangate45: the skill's antenna recipe ladder over its
# diagnosed pool, judged by the SAME sign-off the baseline was judged by.
#
# Attribution discipline -- the reason this arm exists:
#   * The pool is DIAGNOSED, not the corpus: designs whose only DRC violations
#     are antenna and whose LVS is already clean (89 on the frozen baseline).
#   * Everything the baseline used is held fixed: frozen spec table, image,
#     bounds, and the verdict itself comes from signoff45.py -- r2g's
#     run_drc.sh/run_lvs.sh on a restaged layout with the patched deck -- so the
#     two arms are graded by one ruler. Earlier validation runs called klayout
#     and the ORFS LVS target directly; good enough to choose a recipe, not to
#     score an arm.
#   * The grader is untouched. Every rung changes what the ROUTER builds.
#
# The ladder, cheapest first; a design leaves it at the first rung that makes it
# dual-clean:
#   r1  deck_driven_antenna_diode (signoff-loop diagnose_signoff_fix.py): a diode
#       on each net the deck flagged, mapped from the baseline lyrdb through the
#       baseline DEF. Validated 4/4 on typical designs at 18-88 diodes.
#   r2  the same, with the net list ACCUMULATED: r1's nets plus whatever r1's own
#       verdict still flags, mapped through r1's DEF. A first attempt at this rung
#       used only the residual nets on a fresh flow, dropped r1's 377 diodes, and
#       went from 4 violations back to 203 -- so the union is the whole point.
#   r3  ANTENNAAREARATIO 10 in the router's tech LEF (bind-mounted, read back).
#       Brute force: 3/3 cleared, at +11.9% instances. The fallback, not the plan.
#
# Usage: repair_antenna_n45.sh [POOL] ; WORKERS / CORES / ONLY_RUNG via env.
set -uo pipefail
D=/proj/workarea/yangao
# Source run dirs: the frozen baseline by default; an apparatus batch (e.g. the
# pin-floor re-run) for designs whose correct config lives there instead.
RUNS=${RUNS:-$D/rerun_nangate45_20261018/runs}
POOL=${1:-$D/ant_pool.txt}
OUT=${OUT:-$D/r2g_repair_n45}
WORKERS=${WORKERS:-12}
CORES=${CORES:-4}
BOUND=${BOUND:-21600}
IMAGE=localhost/r2g-orfs:26Q3
LEFPATH=/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/nangate45/lef/NangateOpenCellLibrary.tech.lef
MAPPER=${MAPPER:-/tmp/map_antenna_nets.py}
HOOK=${HOOK:-/tmp/deck_antenna_diodes.tcl}
LOG=$OUT/campaign.log
mkdir -p "$OUT"

say() { echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

for f in "$POOL" "$MAPPER" "$HOOK" "$D/signoff45.py"; do
  [ -f "$f" ] || { echo "缺 $f"; exit 2; }
done
# same apparatus as the baseline, or the comparison is void
python3 -I "$D/apparatus_check.py" --manifest "$D/apparatus.json" --platform nangate45 \
  >/dev/null 2>&1 || { echo "器具校验未通过 -- 不开跑"; exit 72; }

map_nets() {  # lyrdb def out_txt -> prints count
  local j=${3%.txt}.json
  python3 -I "$MAPPER" --lyrdb "$1" --def "$2" --json-out "$j" >"${3%.txt}.map.log" 2>&1
  python3 - "$j" "$3" <<'PY'
import json, sys
try:
    nets = json.load(open(sys.argv[1])).get("nets") or []
except Exception:
    nets = []
open(sys.argv[2], "w").write("".join(n + "\n" for n in nets))
print(len(nets))
PY
}

# flow one design under a rung; leaves a signoff45-shaped run dir in $W/runs/$nm
flow() {
  local nm=$1 rung=$2 nets_txt=${3:-}
  local src=$RUNS/$nm W=$OUT/$rung/$nm
  rm -rf "$W"; mkdir -p "$W/work/signoff_fix" "$W/runs/$nm"
  cp "$src/constraint.sdc" "$src/config.mk" "$W/work/"
  # A frontend-repaired source (sv_frontend_repair.sh) reads the slang output
  # at /work/_sv/<top>_slang.v, which lives in that repair's own work dir, one
  # level above its runs/. Carry it over, or the flow has no RTL to read.
  if grep -q '/work/_sv/' "$W/work/config.mk"; then
    cp -r "$(dirname "$RUNS")/work/_sv" "$W/work/" || { echo "sv_source_missing"; return 1; }
  fi
  local dn
  dn=$(grep -oP '(?<=^export DESIGN_NAME = ).*' "$W/work/config.mk" | tr -d ' ')
  local mounts=(-v "$W/work":/work:Z
                -v "$D/rtl-certified-handoff-0002":"$D/rtl-certified-handoff-0002":ro,Z)
  if [ "$rung" = r3 ]; then
    podman run --rm -v "$W/work":/out:Z "$IMAGE" bash -lc \
      "sed 's/ANTENNAAREARATIO 300/ANTENNAAREARATIO 10/' $LEFPATH > /out/tech_r10.lef" >/dev/null 2>&1
    local got
    got=$(podman run --rm -v "$W/work/tech_r10.lef":"$LEFPATH":ro,Z "$IMAGE" \
          bash -lc "grep -oE 'ANTENNAAREARATIO [0-9]+' $LEFPATH | sort -u | head -1" 2>/dev/null | awk '{print $2}')
    [ "$got" = 10 ] || { echo "lef_mount_not_effective:$got"; return 1; }
    mounts+=(-v "$W/work/tech_r10.lef":"$LEFPATH":ro,Z)
  else
    [ -s "$nets_txt" ] || { echo "no_net_list"; return 1; }
    cp "$nets_txt" "$W/work/signoff_fix/antenna_nets.txt" || { echo "net_list_copy_failed"; return 1; }
    cp "$HOOK" "$W/work/signoff_fix/deck_antenna_diodes.tcl"
    echo "export PRE_GLOBAL_ROUTE_TCL = /work/signoff_fix/deck_antenna_diodes.tcl" >> "$W/work/config.mk"
  fi
  local name="rp_${rung}_${nm:0:40}"
  podman rm -f "$name" >/dev/null 2>&1
  # killing the podman CLIENT leaves the container running (the documented
  # leak), so a flow that hits the bound is removed by name
  timeout "$BOUND" podman run --rm --name "$name" "${mounts[@]}" \
    -e NUM_CORES="$CORES" -e OMP_NUM_THREADS="$CORES" "$IMAGE" bash -lc \
    "cd \$ORFS_ROOT/flow && make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work" \
    > "$W/flow.log" 2>&1
  local rc=$?
  [ $rc -eq 124 ] && podman rm -f "$name" >/dev/null 2>&1
  local R=$W/work/results/nangate45/$dn/base
  [ $rc -eq 0 ] && [ -f "$R/6_final.gds" ] || { echo "flow_rc_$rc"; return 1; }
  if [ "$rung" != r3 ]; then
    # proof the hook fired, not the config line that asked it to
    local fired
    fired=$(grep -h "R2G_DIODE: +" "$W"/work/logs/*/*/base/5_1_grt.log 2>/dev/null | wc -l)
    [ "$fired" -gt 0 ] || { echo "hook_not_fired"; return 1; }
  fi
  # signoff45-shaped run dir: the same artifacts the baseline was judged from
  for f in 6_final.def 6_final.gds 6_final.odb 6_final.v 6_final.sdc 6_final.spef; do
    [ -f "$R/$f" ] && cp "$R/$f" "$W/runs/$nm/"
  done
  [ -f "$W/work/results/nangate45/$dn/base/5_route.odb" ] && cp "$R/5_route.odb" "$W/runs/$nm/"
  cp "$W/work/constraint.sdc" "$W/runs/$nm/"
  grep -v '^export PRE_GLOBAL_ROUTE_TCL' "$W/work/config.mk" > "$W/runs/$nm/config.mk"
  python3 - "$src/result.json" "$W/runs/$nm/result.json" "$W/work" <<'PY'
import glob, json, sys
src, dst, w = sys.argv[1:4]
d = json.load(open(src))
for f in glob.glob(w + "/logs/*/*/base/6_report.json"):
    m = json.load(open(f))
    d["cells"] = m.get("finish__design__instance__count") or d.get("cells")
    d["cell_area"] = m.get("finish__design__instance__area")
    d["ws"] = m.get("finish__timing__setup__ws")
    break
d["status"] = "pass"
for k in ("drc_status", "lvs_status", "drc_violations_signoff", "signoff_seconds"):
    d.pop(k, None)
json.dump(d, open(dst, "w"), indent=1)
PY
  # the ladder's own record: how many diodes this rung put in
  grep -c ANTENNA_X1 "$R/6_final.def" > "$W/diodes.txt" 2>/dev/null
  echo ok
}

judge() {  # same ruler as the baseline: signoff45.py on the restaged layout
  local nm=$1 rung=$2 W=$OUT/$1
  W=$OUT/$rung/$nm
  python3 -I "$D/signoff45.py" --runs "$W/runs" --projroot "$W/judge_proj" \
    --out "$W/signoff.json" --workers 1 --timeout "$BOUND" --force > "$W/judge.log" 2>&1
  python3 - "$W/signoff.json" <<'PY'
import json, sys
try:
    r = json.load(open(sys.argv[1]))
    r = (r if isinstance(r, list) else r.get("designs", []))[0]
    print("%s %s %s" % (r.get("drc_status"), r.get("lvs_status"), r.get("drc_violations")))
except Exception as e:
    print("judge_failed - -")
PY
}

one() {
  local nm=$1 rec=$OUT/verdicts/$1.json
  [ -f "$rec" ] && return 0                                  # resumable
  # A design is owned by exactly one worker, across instances. Two workers on
  # one design share its work dir (flow() starts with rm -rf of it) and its
  # container names (a stale same-named container is removed by name), so they
  # destroy each other's live work -- the concrete form of the hard rule
  # against running the same design twice. mkdir is atomic: the first wins.
  mkdir -p "$OUT/locks"
  mkdir "$OUT/locks/$nm" 2>/dev/null || return 0
  # Net lists live OUTSIDE the per-rung work dir. flow() begins with rm -rf of
  # that dir, and the first launch kept r1's list inside it: deleted one line
  # before it was copied, so 12 flows started with no list for the hook to read.
  local N=$OUT/nets/$nm
  mkdir -p "$OUT/verdicts" "$N"
  local hist="" st drc lvs nv n
  # r1: nets from the BASELINE verdict through the BASELINE layout
  n=$(map_nets "$RUNS/$nm/6_drc.lyrdb" "$RUNS/$nm/6_final.def" "$N/r1.txt")
  if [ "${n:-0}" -gt 0 ]; then
    st=$(flow "$nm" r1 "$N/r1.txt")
    if [ "$st" = ok ]; then
      read -r drc lvs nv <<< "$(judge "$nm" r1)"
    else drc=$st; lvs=-; nv=-; fi
  else drc=mapped_zero_nets; lvs=-; nv=-; fi
  hist="r1:$n:$drc/$lvs/$nv"
  if [ "$drc" = clean ] && [ "$lvs" = clean ]; then
    say "  [$nm] 双清 @r1  nets=$n  ($hist)"
    echo "{\"design\":\"$nm\",\"cleared_at\":\"r1\",\"hist\":\"$hist\"}" > "$rec"; return 0
  fi
  # r2: accumulate -- r1's nets plus what r1's own verdict still flags
  if [ "$drc" = violations ] && [ -f "$OUT/r1/$nm/runs/$nm/6_drc.lyrdb" ]; then
    local n2
    n2=$(map_nets "$OUT/r1/$nm/runs/$nm/6_drc.lyrdb" "$OUT/r1/$nm/runs/$nm/6_final.def" "$N/r1_residual.txt")
    sort -u "$N/r1.txt" "$N/r1_residual.txt" > "$N/r2.txt"
    n=$(wc -l < "$N/r2.txt")
    st=$(flow "$nm" r2 "$N/r2.txt")
    if [ "$st" = ok ]; then read -r drc lvs nv <<< "$(judge "$nm" r2)"
    else drc=$st; lvs=-; nv=-; fi
    hist="$hist r2:$n(+$n2):$drc/$lvs/$nv"
    if [ "$drc" = clean ] && [ "$lvs" = clean ]; then
      say "  [$nm] 双清 @r2  nets=$n  ($hist)"
      echo "{\"design\":\"$nm\",\"cleared_at\":\"r2\",\"hist\":\"$hist\"}" > "$rec"; return 0
    fi
  fi
  # r3: the expensive fallback
  st=$(flow "$nm" r3)
  if [ "$st" = ok ]; then read -r drc lvs nv <<< "$(judge "$nm" r3)"
  else drc=$st; lvs=-; nv=-; fi
  hist="$hist r3:$drc/$lvs/$nv"
  if [ "$drc" = clean ] && [ "$lvs" = clean ]; then
    say "  [$nm] 双清 @r3  ($hist)"
    echo "{\"design\":\"$nm\",\"cleared_at\":\"r3\",\"hist\":\"$hist\"}" > "$rec"; return 0
  fi
  say "  [$nm] 未清  ($hist)"
  echo "{\"design\":\"$nm\",\"cleared_at\":null,\"hist\":\"$hist\"}" > "$rec"
}

say "=== r2g 修复臂 nangate45  池子 $(grep -c . "$POOL") 份  并发 $WORKERS×$CORES 核  上限 ${BOUND}s"
say "    deck $(sha256sum "$D/drcfix/FreePDK45_tiled.lydrc" | cut -c1-12)  判决: signoff45.py（与基线同一把尺子）"
while read -r nm; do
  [ -z "$nm" ] && continue
  while [ "$(jobs -rp | wc -l)" -ge "$WORKERS" ]; do sleep 15; done
  one "$nm" &
done < "$POOL"
wait
say "=== 完成  双清 $(grep -l '"cleared_at":"r' "$OUT"/verdicts/*.json 2>/dev/null | wc -l) / $(ls "$OUT"/verdicts/*.json 2>/dev/null | wc -l)"
