#!/usr/bin/env bash
# Shared backend-run resolver + signoff provenance record (RMD-P0-02,
# three-platform pilot 2026-07-22; see failure-patterns.md "Strict-Gate Pilot
# Failures").
#
# Sourced (not executed) by _restage_for_signoff.sh, run_drc.sh, run_lvs.sh,
# run_netgen_lvs.sh, run_rcx.sh — ONE resolver so every checker, the artifact
# copy-back, and report extraction all name the SAME backend run. Before this,
# the restage picked the newest RUN with a 6_final.gds while the copy-back used
# `ls | sort | tail -1` (any newest RUN, even an empty crashed one) and
# report_io.py fell back to the same latest-run guess — three different answers
# for "which run did signoff grade?".
#
# Provides:
#   r2g_pick_backend_run <project_dir>
#       Echo the RUN_* dir that owns the layout signoff must grade: the newest
#       RUN with results/6_final.gds, else the newest with final/6_final.gds
#       (older r2g layout). rc 1 when no run has a final GDS.
#   r2g_write_signoff_record <project_dir> <run_dir> [platform] [flow_variant]
#       Write <project>/backend/.r2g_signoff_run — the project-side JSON record
#       report_io.run_provenance() reads (source 'signoff_record'). Carries the
#       run tag AND the sha256 of the run's 6_final.gds/.def so a report is
#       bound to exact artifact bytes, not just a directory name. This replaces
#       the dead `backend/RUN_*/.r2g_restaged` glob (the marker was only ever
#       written into the ORFS workspace, so every report silently degraded to
#       the 'latest_run' guess — the 12/12 weak-binding defect of the
#       2026-07-22 three-platform pilot).

_R2G_BACKEND_RUN_LIB=1

r2g_pick_backend_run() {
  local _project_dir="$1" _run_dir
  for _run_dir in $(ls -dt "$_project_dir"/backend/RUN_* 2>/dev/null); do
    if [[ -f "$_run_dir/results/6_final.gds" ]]; then
      echo "$_run_dir"
      return 0
    fi
  done
  for _run_dir in $(ls -dt "$_project_dir"/backend/RUN_* 2>/dev/null); do
    if [[ -f "$_run_dir/final/6_final.gds" ]]; then
      echo "$_run_dir"
      return 0
    fi
  done
  return 1
}

r2g_write_signoff_record() {
  local _project_dir="$1" _run_dir="$2" _platform="${3:-}" _variant="${4:-}"
  [[ -n "$_run_dir" && -d "$_run_dir" ]] || return 0
  mkdir -p "$_project_dir/backend" 2>/dev/null || return 0
  python3 - "$_project_dir/backend/.r2g_signoff_run" "$_run_dir" "$_platform" "$_variant" <<'PYEOF' || true
import hashlib, json, os, sys, time

out, run_dir, platform, variant = sys.argv[1:5]

def _sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None

record = {
    "run_tag": os.path.basename(os.path.realpath(run_dir)),
    "run_dir": os.path.realpath(run_dir),
    "platform": platform or None,
    "flow_variant": variant or None,
    "recorded_at": int(time.time()),
}
for key, name in (("gds_sha256", "6_final.gds"), ("def_sha256", "6_final.def")):
    for sub in ("results", "final"):
        p = os.path.join(run_dir, sub, name)
        if os.path.isfile(p):
            record[key] = _sha256(p)
            break
    else:
        record[key] = None

tmp = out + f".tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(record, f, indent=1)
    f.write("\n")
os.replace(tmp, out)
PYEOF
}
