#!/usr/bin/env bash
set -euo pipefail
# usage: fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both|route|timing] [--max-iters N] [--resume]
#
# Iteratively applies REAL layout fixes for DRC/LVS violations: diagnose →
# apply (config.mk marked block) → re-run flow → re-check → compare, up to
# --max-iters, with early-exit when an iteration does not reduce the count.
# Real-fixes-only: never relaxes the DRC rule deck. See references/signoff-fixing.md.
#
# Progress is flushed to <project>/reports/fix_log.jsonl per iteration (long
# DRC/LVS runtimes mean we never batch logging to the end); a human-readable
# <project>/reports/fix_summary.md is written at the end.
#
# Command seams are overridable for testing:
#   R2G_RUN_ORFS R2G_RUN_DRC R2G_RUN_LVS R2G_EXTRACT_DRC R2G_EXTRACT_LVS R2G_DIAGNOSE

# --- Tier-0 journal hooks (engineer-loop spec §5.2) — never break the flow ---
KNOWLEDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../knowledge" && pwd)"
export R2G_KNOWLEDGE_DIR="$KNOWLEDGE_DIR"

_journal_knob_deltas() {  # config_edits_json strategy_id [symptom_id] [parent_action_id]
  # One config_knob_delta action per knob, linked to symptom_id (Gap 3) and — for
  # iteration 2+ — parent_action_id (Gap 4, stacked-fix chain). PRINTS the FIRST
  # action_id to stdout so the caller can pass it as the parent of the next
  # iteration. Best-effort: honors R2G_JOURNAL=0 and never breaks the flow.
  python3 - "$1" "$2" "$PROJECT_DIR" "$FIX_SESSION_ID" "${3:-}" "${4:-}" <<'PYEOF' 2>/dev/null || true
import json, os, sys
if os.environ.get("R2G_JOURNAL", "1") == "0":
    sys.exit(0)
edits = json.loads(sys.argv[1] or "{}")
strat, proj, sess = sys.argv[2], sys.argv[3], sys.argv[4]
symptom_id = sys.argv[5] or None
parent = int(sys.argv[6]) if (len(sys.argv) > 6 and sys.argv[6]) else None
sys.path.insert(0, os.environ.get("R2G_KNOWLEDGE_DIR", ""))
try:
    import journal_db
    conn = journal_db.connect(os.environ.get("R2G_JOURNAL_DB") or journal_db.DEFAULT_JOURNAL_PATH)
    journal_db.ensure_schema(conn)
    first = None
    for knob, new in edits.items():
        aid = journal_db.append_action(
            conn, project_path=proj, actor="loop", action_type="config_knob_delta",
            payload={"knob": knob, "new": str(new), "strategy": strat},
            fix_session_id=sess, symptom_id=symptom_id, parent_action_id=parent)
        if first is None:
            first = aid
    conn.close()
    if first is not None:
        print(first)
except Exception as exc:                      # never break the flow
    print(f"WARNING: journal knob deltas skipped: {exc}", file=sys.stderr)
PYEOF
}

_compute_symptom_id() {  # check vclass [predicates_json] -> 16-hex symptom_id (or empty)
  # Mirror the ingester's symptom_id recipe EXACTLY (symptom.canonical_signature ->
  # symptom_id incl. the route->orfs_stage remap) so the journal symptom_id and the
  # knowledge symptom_id agree. Empty output on any error (best-effort linkage).
  python3 - "$1" "$2" "${3:-}" <<'PYEOF' 2>/dev/null || true
import json, os, sys
sys.path.insert(0, os.environ.get("R2G_KNOWLEDGE_DIR", ""))
try:
    import symptom
    check, vclass = sys.argv[1], sys.argv[2]
    preds = json.loads(sys.argv[3] or "{}")
    if check == "route":                      # backend abort: keyed under orfs_stage
        check, vclass = "orfs_stage", (vclass or "route")
    sig = symptom.canonical_signature(check, vclass or None, preds)
    print(symptom.symptom_id(sig))
except Exception:
    pass
PYEOF
}

_journal_action() {  # action_type payload_json [symptom_id] — generic best-effort action
  local args=(action --project "$PROJECT_DIR" --actor loop --type "$1"
              --session "$FIX_SESSION_ID" --payload "$2")
  [[ -n "${3:-}" ]] && args+=(--symptom "$3")
  [[ -n "${R2G_JOURNAL_DB:-}" ]] && args+=(--db "$R2G_JOURNAL_DB")
  python3 "$R2G_KNOWLEDGE_DIR/journal_action.py" "${args[@]}" >/dev/null 2>&1 || true
}

# Test seam: allow sourcing helpers without executing the flow/arg-parse/exit.
[[ "${R2G_SOURCE_ONLY:-0}" == "1" ]] && return 0 2>/dev/null
# --- end Tier-0 journal hooks ---

PROJECT_DIR=""; PLATFORM="asap7"; CHECK="both"; MAX_ITERS=8; BASE_ITERS=3; RESUME=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK="$2"; shift 2;;
    --max-iters) MAX_ITERS="$2"; shift 2;;
    --resume) RESUME=1; shift;;  # legacy no-op: stage-scoped resume is the default (see #35)
    -*) echo "unknown flag: $1" >&2; exit 1;;
    *) if [[ -z "$PROJECT_DIR" ]]; then PROJECT_DIR="$1"; else PLATFORM="$1"; fi; shift;;
  esac
done
[[ -z "$PROJECT_DIR" ]] && { echo "usage: fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both|route|timing] [--max-iters N] [--resume]" >&2; exit 1; }
[[ "$CHECK" =~ ^(drc|lvs|both|route|timing)$ ]] || { echo "ERROR: --check must be drc|lvs|both|route|timing" >&2; exit 1; }
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTRACT_DIR="$(cd "$SCRIPT_DIR/../extract" && pwd)"
REPORTS_DIR_SCRIPTS="$(cd "$SCRIPT_DIR/../reports" && pwd)"
RUN_ORFS="${R2G_RUN_ORFS:-$SCRIPT_DIR/run_orfs.sh}"
RUN_DRC="${R2G_RUN_DRC:-$SCRIPT_DIR/run_drc.sh}"
# LVS tool selection is PLATFORM-AWARE. On sky130 the production LVS path is
# Netgen (Magic GDS extraction + Netgen compare): the ORFS KLayout sky130 rule
# deck (`sky130hd_r2g.lylvs`) is NOT production-grade — it flattens std-cell
# subcircuits and reports false "Netlists don't match" on designs Netgen finds
# clean (validated 2026-06-17: wbsafety + blob_merge KLayout-fail -> Netgen-clean).
# Routing the autonomous loop through KLayout LVS on sky130 was escalating
# already-clean designs as lvs:fail. KLayout LVS stays the default everywhere
# else (nangate45/gf180/ihp). An explicit R2G_RUN_LVS override always wins.
# See references/failure-patterns.md "sky130 LVS".
if [[ -n "${R2G_RUN_LVS:-}" ]]; then
  RUN_LVS="$R2G_RUN_LVS"
elif [[ "$PLATFORM" == sky130* ]]; then
  RUN_LVS="$SCRIPT_DIR/run_netgen_lvs.sh"
else
  RUN_LVS="$SCRIPT_DIR/run_lvs.sh"
fi
EXTRACT_DRC="${R2G_EXTRACT_DRC:-$EXTRACT_DIR/extract_drc.py}"
EXTRACT_LVS="${R2G_EXTRACT_LVS:-$EXTRACT_DIR/extract_lvs.py}"
EXTRACT_ROUTE="${R2G_EXTRACT_ROUTE:-$EXTRACT_DIR/extract_route.py}"
# timing baseline needs reports/ppa.json, which NO flow step emits (run_orfs writes only
# the backend; extract_ppa.py must be invoked explicitly). Without this, check_timing
# finds no ppa.json -> tier 'unknown' -> diagnose picks NO strategy -> period_relax never
# applies and the timing arm can't diverge (2026-06-25 live-verify root cause).
EXTRACT_PPA="${R2G_EXTRACT_PPA:-$EXTRACT_DIR/extract_ppa.py}"
DIAGNOSE="${R2G_DIAGNOSE:-$REPORTS_DIR_SCRIPTS/diagnose_signoff_fix.py}"
# timing has no separate signoff tool: the run_orfs reflow IS the check, and
# check_timing.py re-measures it from the reflowed reports/ppa.json (analogous to
# how route's extract_route reads the route stage). Overridable for testing.
CHECK_TIMING="${R2G_CHECK_TIMING:-$REPORTS_DIR_SCRIPTS/check_timing.py}"
KNOWLEDGE_DIR="$(cd "$SCRIPT_DIR/../../knowledge" && pwd)"
export R2G_KNOWLEDGE_DIR="$KNOWLEDGE_DIR"

REPORTS="$PROJECT_DIR/reports"
mkdir -p "$REPORTS"
LOG="$REPORTS/fix_log.jsonl"

# Stable episode id for this fixing run (spec §5.1). All iterations of this
# invocation share it; the ingester groups fix_events by it.
FIX_SESSION_ID="$(python3 -c 'import hashlib,sys,time
h=hashlib.sha1(); h.update(sys.argv[1].encode()); h.update(sys.argv[2].encode())
h.update(str(time.time()).encode()); print(h.hexdigest()[:16])' "$PROJECT_DIR" "$CHECK")"

_count() {  # read current violation count from a report json
  python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
v=d.get("total_violations"); v=d.get("mismatch_count") if v is None else v
# timing: timing_check.json has no violation count — emit a NON-NEGATIVE badness
# that is 0 iff timing is MET (wns>=0) and grows with worse slack, so fix_one`s
# before/after improvement logic (after==0 => CLEAN) works unchanged.
if v is None and ("tier" in d or "wns" in d or "wns_ns" in d):
    w=d.get("wns", d.get("wns_ns"))
    if isinstance(w,(int,float)): v=round(max(0.0,-float(w))*1000)
print("" if v is None else v)' "$1" 2>/dev/null || echo ""
}

_run_extract() {  # $1 = drc|lvs|route|timing
  # timing: no extract — check_timing.py re-measures the reflowed reports/ppa.json
  # into reports/timing_check.json (the rerun IS the check). It exits 1 when timing
  # is not met (moderate/severe/unconstrained) and 2 on missing data; neither is a
  # script error during fixing, so swallow the rc and let _count read the tier.
  if [[ "$1" == "timing" ]]; then
    # Regenerate reports/ppa.json from the (reflowed) backend FIRST — check_timing reads
    # it and no other step emits it. Then re-measure timing into timing_check.json.
    "$EXTRACT_PPA" "$PROJECT_DIR" "$REPORTS/ppa.json" >/dev/null 2>&1 || true
    "$CHECK_TIMING" "$PROJECT_DIR" >/dev/null 2>&1 || true
    return 0
  fi
  local script
  case "$1" in
    drc)   script="$EXTRACT_DRC";;
    route) script="$EXTRACT_ROUTE";;
    *)     script="$EXTRACT_LVS";;
  esac
  "$script" "$PROJECT_DIR" "$REPORTS/$1.json"
}

_snapshot() {  # $1 = drc|lvs : echo "<violation_class>\t<categories_json>" for the
  # CURRENT (pre-fix) report. Captured before a fix is applied / extract overwrites
  # the report, so _log_iter records the BEFORE-iteration violation snapshot rather
  # than the post-fix (clean) report. See CRITICAL CORRECTION in the Task 3 plan.
  python3 -c 'import json,sys,os
check=sys.argv[1]; proj=sys.argv[2]
if check=="timing":
    # Timing has no per-category vector and no <check>.json report (the report is
    # reports/timing_check.json): violation_class is "timing"; categories {}.
    print("timing\t"+json.dumps({})); sys.exit(0)
rep=os.path.join(proj,"reports",check+".json")
try: d=json.load(open(rep))
except Exception: d=None
if d is None:
    print("\t"); sys.exit(0)
if check=="drc":
    cats=d.get("categories") or {}
    dom=max(cats,key=lambda k:(cats[k].get("count") or 0)) if cats else ""
    print((dom or "")+"\t"+json.dumps(cats))
elif check=="route":
    # Backend-abort: the violation_class is the stage ("route"); no per-category vector.
    print("route\t"+json.dumps({"total_violations":d.get("total_violations")}))
else:
    print((d.get("mismatch_class") or "")+"\t"+json.dumps({"mismatch_count":d.get("mismatch_count")}))' \
    "$1" "$PROJECT_DIR"
}

_predicates_snapshot() {  # $1 = drc|lvs : symptom predicates JSON from the CURRENT
  # (pre-fix) report, captured by fix_one BEFORE apply/extract overwrites it, so the
  # logged symptom describes the violation being fixed (not the post-fix state).
  python3 -c 'import json,sys,os
check=sys.argv[1]; proj=sys.argv[2]
if check=="timing":
    # Timing carries no curated boolean predicates in symptom v1 (the tier is the
    # violation_class). Mirror the route case: empty predicates.
    print(json.dumps({})); sys.exit(0)
rep=os.path.join(proj,"reports",check+".json")
try: report=json.load(open(rep))
except Exception: report={}
preds={}
try:
    sys.path.insert(0, os.environ.get("R2G_KNOWLEDGE_DIR",""))
    import symptom
    preds=symptom.predicates_for(check, report)
except Exception: preds={}
print(json.dumps(preds))' "$1" "$PROJECT_DIR"
}

_log_iter() {  # check iter strategy before after verdict from_stage vclass before_cats config_delta
  python3 -c 'import json,sys,os
check,it,strategy,before,after,verdict,from_stage=sys.argv[1:8]
proj,sid,logp=sys.argv[8],sys.argv[9],sys.argv[10]
vclass,before_cats_json,ts=sys.argv[11],sys.argv[12],sys.argv[13]
config_delta=(sys.argv[14] if len(sys.argv)>14 else "") or "{}"
rep=os.path.join(proj,"reports",check+".json")
try: report=json.load(open(rep))
except Exception: report={}
status=report.get("status")
# Symptom predicates describe the BEFORE-fix violation we set out to repair, so
# prefer the snapshot fix_one captured from the pre-fix report (R2G_LOG_PREDICATES).
# Only fall back to deriving from the CURRENT report (post-fix at log time) when the
# snapshot is absent — otherwise an applied fix that shifts e.g. net balance would
# mis-tag this iteration symptom_id. knowledge/ is on sys.path via R2G_KNOWLEDGE_DIR.
preds={}
_pre=os.environ.get("R2G_LOG_PREDICATES")
if _pre:
    try: preds=json.loads(_pre)
    except Exception: preds={}
else:
    try:
        sys.path.insert(0, os.environ.get("R2G_KNOWLEDGE_DIR",""))
        import symptom
        preds=symptom.predicates_for(check, report)
    except Exception: preds={}
env_keys=("PLACE_FAST","ROUTE_FAST","SKIP_ANTENNA_REPAIR","ROUTE_FAST_DRT_ITERS")
env_flags={k:os.environ[k] for k in env_keys if k in os.environ}
# A route abort is the backend-stage analogue of a DRC/LVS violation. The symptom
# index (symptom.py) keys ALL backend-stage aborts under check=orfs_stage with the
# STAGE as the class, so the fix_event/run_violations symptom_ids agree. Map the
# fix-loop check name ("route") to that canonical symptom check for the log row.
if check=="route":
    check="orfs_stage"; vclass=vclass or "route"
cum={}
cfgp=os.path.join(proj,"constraints","config.mk")
if os.path.exists(cfgp):
    inblk=False
    for ln in open(cfgp):
        s=ln.strip()
        if s=="# >>> r2g signoff-fix (auto) >>>": inblk=True; continue
        if s=="# <<< r2g signoff-fix (auto) <<<": inblk=False; continue
        if inblk and s.startswith("export "):
            kv=s[len("export "):].split("=",1)
            if len(kv)==2: cum[kv[0].strip()]=kv[1].strip()
o=dict(check=check,iter=int(it),strategy=strategy,
       before=(before or None),after=(after or None),verdict=verdict,
       from_stage=(from_stage or None),fix_session_id=sid,
       violation_class=(vclass or None),after_status=status,
       before_categories=(before_cats_json if before_cats_json else None),
       cumulative_config=json.dumps(cum,sort_keys=True),
       config_delta=config_delta, env_flags=json.dumps(env_flags,sort_keys=True),
       predicates=preds, ts=ts)
open(logp,"a").write(json.dumps(o)+"\n")' \
    "$1" "$2" "$3" "$4" "$5" "$6" "${7:-}" "$PROJECT_DIR" "$FIX_SESSION_ID" "$LOG" \
    "${8:-}" "${9:-}" "$(date +%FT%T%:z)" "${10:-}"
}

_ensure_baseline() {  # $1 = drc|lvs : RUN the signoff tool once if there is no real
  # baseline. A design freshly produced by run_orfs has NO Magic-DRC / Netgen-LVS
  # report yet, so _run_extract yields status "unknown"; diagnose then STOPs and the
  # check is SILENTLY SKIPPED (never run). Establish a real baseline by invoking the
  # signoff tool when the report is missing or its status is empty/unknown. Route is
  # exempt: its baseline is the flow's own route stage, not a separate tool.
  #
  # STALENESS (2026-06-18): also (re)run the baseline when a backend GDS is NEWER
  # than the stored report — the design was re-flowed since the last signoff, so the
  # stored verdict (even a definite clean/fail) describes a layout that no longer
  # exists. Without this, an A/B arm dir (copied from the base project WITH its old
  # KLayout lvs.json='fail') or any route_relief reflow keeps the stale verdict and
  # the real signoff tool (e.g. Netgen on sky130) never runs on the new layout —
  # producing a false escalation. A fresh GDS needs fresh signoff.
  local check="$1" report="$REPORTS/$1.json" st="" stale=0 newest_gds
  [[ "$check" == "route" ]] && return 0
  # timing: no separate signoff tool. The baseline is check_timing.py reading the
  # already-present reports/ppa.json into reports/timing_check.json (the rerun IS
  # the check). Establish it via _run_extract, which swallows the decision-needed rc.
  [[ "$check" == "timing" ]] && { _run_extract timing; return 0; }
  [[ -f "$report" ]] && st="$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("status") or "")
except Exception: print("")' "$report" 2>/dev/null)"
  newest_gds="$(ls -t "$PROJECT_DIR"/backend/RUN_*/final/*.gds 2>/dev/null | head -1)"
  [[ -n "$newest_gds" && ( ! -f "$report" || "$newest_gds" -nt "$report" ) ]] && stale=1
  if [[ -z "$st" || "$st" == "unknown" || "$stale" == "1" ]]; then
    echo "[$check] (re)establish baseline signoff (status='${st:-missing}', stale_vs_gds=$stale) — running $check"
    if [[ "$check" == "drc" ]]; then "$RUN_DRC" "$PROJECT_DIR" "$PLATFORM" || true
    else "$RUN_LVS" "$PROJECT_DIR" "$PLATFORM" || true; fi
    _run_extract "$check" || true
  fi
}

fix_one() {  # $1 = drc|lvs|route|timing
  local check="$1" report="$REPORTS/$1.json" tried="" before after it sid rerun recheck line verdict
  local noimp=0 before_vclass before_cats snap sym root_aid="" first_aid
  local antenna_noimp=0 antenna_marker="$REPORTS/antenna_nonconverged.json"
  # timing's report is reports/timing_check.json (check_timing.py's output), not
  # reports/timing.json — point _count/_run_extract at the real file.
  [[ "$check" == "timing" ]] && report="$REPORTS/timing_check.json"
  _ensure_baseline "$check"
  [[ -f "$report" ]] || _run_extract "$check"
  before="$(_count "$report")"
  # Antenna non-convergence memory (failure-patterns.md #36): a prior session
  # already proved the antenna strategies don't move this design's residual —
  # exclude them up front instead of silently burning the same diode+reroute
  # reflows on every later visit (SHA-1/SHA-256 loop). Retry deliberately with
  # R2G_FIX_RETRY_NONCONVERGED=1 (e.g. after an ORFS/OpenROAD update), or the
  # marker clears itself the moment the check reaches CLEAN.
  if [[ "$check" == "drc" && -f "$antenna_marker" && "${R2G_FIX_RETRY_NONCONVERGED:-0}" != "1" ]]; then
    local marker_excl
    marker_excl="$(python3 -c 'import json,sys
try: print(",".join(json.load(open(sys.argv[1])).get("strategies_tried") or []))
except Exception: print("")' "$antenna_marker")"
    if [[ -n "$marker_excl" ]]; then
      tried="${tried:+$tried,}$marker_excl"
      echo "[$check] antenna repair previously NON-CONVERGED — excluding: $marker_excl" \
           "(R2G_FIX_RETRY_NONCONVERGED=1 to retry; marker: reports/antenna_nonconverged.json)"
    fi
  fi
  for ((it=1; it<=MAX_ITERS; it++)); do
    # Snapshot the PRE-fix dominant violation_class + full categories vector for
    # this iteration NOW, before any fix/extract overwrites the report (the
    # applied iteration's extract clobbers it with the post-fix result).
    snap="$(_snapshot "$check")"
    before_vclass="${snap%%$'\t'*}"; before_cats="${snap#*$'\t'}"
    # Snapshot the BEFORE-fix symptom predicates too (same pre-fix report); _log_iter
    # reads them via R2G_LOG_PREDICATES so the symptom_id reflects the violation we
    # set out to fix, not the post-fix report it would otherwise re-read at log time.
    export R2G_LOG_PREDICATES="$(_predicates_snapshot "$check")"
    # Gap 3: the symptom_id this iteration is fixing (same recipe the ingester
    # uses), so config_knob_delta / stage_rerun journal rows link to the symptom.
    sym="$(_compute_symptom_id "$check" "$before_vclass" "$R2G_LOG_PREDICATES")"
    local all_excl="${tried}${R2G_FIX_EXCLUDE:+${tried:+,}$R2G_FIX_EXCLUDE}"
    line="$("$DIAGNOSE" "$PROJECT_DIR" --check "$check" --exclude "$all_excl" \
            ${R2G_FIX_RANK_FIRST:+--rank-first "$R2G_FIX_RANK_FIRST"} --next)"
    # Split on tab WITHOUT collapsing empty middle fields. `read` with a
    # whitespace IFS (tab) would merge consecutive tabs, dropping an empty
    # rerun_from column and shifting recheck into rerun; map tabs to a
    # non-whitespace unit-separator first so empty fields are preserved.
    IFS=$'\x1f' read -r sid rerun recheck <<<"${line//$'\t'/$'\x1f'}"
    [[ -n "$sid" ]] || { echo "[$check] ERROR: diagnose returned empty output; aborting" >&2; return 1; }
    if [[ "$sid" == "STOP" ]]; then
      _log_iter "$check" "$it" "none" "$before" "$before" "stop_${rerun}" "" "$before_vclass" "$before_cats" "{}"
      echo "[$check] stop: $rerun ${recheck:-}"; return 0
    fi
    echo "[$check] iter $it: applying $sid (rerun_from=${rerun:-none})"
    local apply_out cfg_delta="{}"
    if ! apply_out="$("$DIAGNOSE" "$PROJECT_DIR" --check "$check" --apply "$sid")"; then
      echo "[$check] apply '$sid' failed; aborting" >&2
      _log_iter "$check" "$it" "$sid" "$before" "$before" "apply_failed" "$rerun" "$before_vclass" "$before_cats" "{}"; return 1
    fi
    cfg_delta="$(python3 -c 'import json,sys
try: print(json.dumps(json.loads(sys.stdin.read()).get("config_edits") or {}))
except Exception: print("{}")' <<<"$apply_out")"
    # Gap 3+4: stamp symptom_id on each knob row; chain iteration 2+ to the first
    # iteration's action via parent_action_id (the first call prints its action_id).
    first_aid="$(_journal_knob_deltas "$cfg_delta" "$sid" "$sym" "$root_aid")"
    [[ -z "$root_aid" && -n "$first_aid" ]] && root_aid="$first_aid"
    tried="${tried:+$tried,}$sid"
    if [[ -n "$rerun" ]]; then
      # Tier B4: a stage re-run is a loop decision — journal it (symptom-linked).
      _journal_action stage_rerun "$(python3 -c 'import json,sys;print(json.dumps({"from_stage":sys.argv[1],"strategy":sys.argv[2]}))' "$rerun" "$sid")" "$sym"
      local rc=0
      # Stage-scoped resume is the DEFAULT now (failure-patterns.md #35):
      # run_orfs invalidates the resumed stage (make clean_<stage>) so the
      # just-applied config edit is GUARANTEED to take effect while every
      # earlier stage's artifacts are reused. The old default rebuilt
      # synth->finish (clean_all) on every fix iteration — necessary back then
      # because a plain resume silently NO-OPed the edit (config.mk is not a
      # make prerequisite). R2G_FIX_FULL_REFLOW=1 restores the full rebuild
      # (use when an edit affects a stage EARLIER than the strategy's declared
      # rerun_from). --resume is kept as a no-op alias of the default.
      # Thread the concrete rerun reason into the run's own stage_log/flow.log
      # (failure-patterns.md #38 / codex #3) — the strategy is already journaled
      # to journal.sqlite above, but a reviewer reading backend/RUN_*/ sees why.
      local rerun_reason="signoff fix: strategy=$sid rerun_from=${rerun:-full} (config edit)"
      if [[ "${R2G_FIX_FULL_REFLOW:-0}" == "1" ]]; then
        R2G_RERUN_REASON="$rerun_reason (full reflow)" "$RUN_ORFS" "$PROJECT_DIR" "$PLATFORM" || rc=$?
      else
        FROM_STAGE="$rerun" R2G_RERUN_REASON="$rerun_reason" "$RUN_ORFS" "$PROJECT_DIR" "$PLATFORM" || rc=$?
      fi
      if [[ $rc -ne 0 ]]; then
        echo "[$check] run_orfs failed (rc=$rc); aborting this check" >&2
        _log_iter "$check" "$it" "$sid" "$before" "$before" "rerun_failed_rc$rc" "$rerun" "$before_vclass" "$before_cats" "{}"
        return 1
      fi
    fi
    # route/timing: the rerun (run_orfs from floorplan/synth) IS the check — there
    # is no separate signoff tool. extract_route reads the backend route stage;
    # _run_extract timing re-measures the reflowed ppa.json via check_timing.py.
    if [[ "$check" == "drc" ]]; then "$RUN_DRC" "$PROJECT_DIR" "$PLATFORM" || true
    elif [[ "$check" == "lvs" ]]; then "$RUN_LVS" "$PROJECT_DIR" "$PLATFORM" || true; fi
    _run_extract "$check"
    after="$(_count "$report")"
    if [[ -z "$after" ]]; then
      # #14 phantom-win guard: the re-check produced no parseable count (extract
      # crashed / report unparseable). This is NOT evidence of a win — record a
      # non-evidence verdict (ingester _VERDICT_MAP fall-through -> 'inconclusive')
      # instead of leaving verdict='applied' (which maps to 'win'). See
      # references/signoff-fixing.md. Skip the no_improvement/cleared logic.
      verdict="recheck_unparsed"; noimp=0
      _log_iter "$check" "$it" "$sid" "$before" "$after" "$verdict" "$rerun" "$before_vclass" "$before_cats" "{}"
      echo "[$check] iter $it: $before -> ? ($verdict)"
      echo "[$check] re-check produced no parseable count; trying next strategy"
      continue
    fi
    verdict="applied"
    # Is THIS iteration antenna-scoped? Drives the CONSECUTIVE antenna-noimp
    # counter below (failure-patterns.md #38).
    local is_antenna_iter=0
    [[ "$sid" == antenna* || "$before_vclass" == *[Aa]ntenna* ]] && is_antenna_iter=1
    if [[ -n "$before" ]] && python3 -c "import sys;sys.exit(0 if float('$after')>=float('$before') else 1)" 2>/dev/null; then
      verdict="no_improvement"; noimp=$((noimp+1))
      (( is_antenna_iter )) && antenna_noimp=$((antenna_noimp+1))
    else
      noimp=0
      # An IMPROVING antenna strategy RESETS the consecutive counter: a design
      # converging via interleaved wins and no-ops (10->5 win, 5->5 no-op,
      # 5->3 win, 3->3 no-op) must NOT be falsely declared non-converged
      # (the cumulative-vs-consecutive over-abort, failure-patterns.md #38).
      (( is_antenna_iter )) && antenna_noimp=0
    fi
    [[ "$after" == "0" ]] && verdict="cleared"
    # Antenna non-convergence auto-exit (failure-patterns.md #36): each antenna
    # strategy already drives up to MAX_REPAIR_ANTENNAS_ITER_DRT diode+reroute
    # rounds INSIDE ORFS with no improvement check of its own, and OpenROAD's
    # antenna model can disagree with the signoff deck — so the same 1-2
    # residual violations can survive every round (the SHA-1/SHA-256 loop).
    # Two CONSECUTIVE non-improving antenna strategies = NON-CONVERGED: terminal
    # verdict, persistent marker, stop burning reflows.
    if [[ "$verdict" == "no_improvement" ]] && (( is_antenna_iter )); then
      (( antenna_noimp >= 2 )) && verdict="antenna_nonconverged"
    fi
    _log_iter "$check" "$it" "$sid" "$before" "$after" "$verdict" "$rerun" "$before_vclass" "$before_cats" "$cfg_delta"
    echo "[$check] iter $it: $before -> $after ($verdict)"
    if [[ "$after" == "0" ]]; then
      # A clean check invalidates any recorded antenna non-convergence.
      [[ "$check" == "drc" ]] && rm -f "$antenna_marker"
      echo "[$check] CLEAN"; return 0
    fi
    if [[ "$verdict" == "antenna_nonconverged" ]]; then
      python3 -c 'import json,sys
out, residual, iters, tried = sys.argv[1:5]
json.dump({"class": "antenna", "residual_count": float(residual) if residual else None,
           "fix_iters": int(iters), "strategies_tried": [s for s in tried.split(",") if s],
           "hint": "OpenROAD antenna repair cannot close this residual (model disagrees "
                   "with the signoff deck or an irreducible multi-gate net). Retry "
                   "deliberately with R2G_FIX_RETRY_NONCONVERGED=1 after a toolchain "
                   "change, or accept/escalate the residual."},
          open(out, "w"), indent=1)' "$antenna_marker" "$after" "$it" "$tried"
      echo "[$check] ANTENNA REPAIR NON-CONVERGED: $after residual violation(s) unchanged" \
           "after $antenna_noimp antenna strategies — stopping (marker: reports/antenna_nonconverged.json)"
      return 0
    fi
    if [[ "$verdict" == "no_improvement" ]]; then echo "[$check] no improvement; trying next strategy"; fi
    # Adaptive budget (D12): past base, stop after 2 consecutive non-improving iters.
    if (( it >= BASE_ITERS && noimp >= 2 )); then
      echo "[$check] $noimp non-improving past base $BASE_ITERS; stopping"; return 0
    fi
    before="$after"
  done
  echo "[$check] reached max-iters=$MAX_ITERS"
}

: > "$LOG"
# route is the backend-abort check: fix BEFORE signoff (a route abort never reaches
# drc/lvs). It is its own --check value (not part of "both").
[[ "$CHECK" == "route" ]] && fix_one route || true
# timing is its own check (NOT part of "both"): the rerun (run_orfs from synth)
# IS the check; check_timing.py re-measures the reflowed ppa.json.
[[ "$CHECK" == "timing" ]] && fix_one timing || true
[[ "$CHECK" == "drc" || "$CHECK" == "both" ]] && fix_one drc || true
[[ "$CHECK" == "lvs" || "$CHECK" == "both" ]] && fix_one lvs || true

# Markdown summary from the JSONL log
python3 -c 'import json,sys
log=sys.argv[1]; out=sys.argv[2]
rows=[json.loads(l) for l in open(log) if l.strip()]
lines=["# Signoff fix summary","","| check | iter | strategy | before | after | verdict |","|---|---|---|---|---|---|"]
def c(v): return "" if v is None else v
for r in rows:
    lines.append("| {} | {} | {} | {} | {} | {} |".format(
        c(r.get("check")), c(r.get("iter")), c(r.get("strategy")),
        c(r.get("before")), c(r.get("after")), c(r.get("verdict"))))
open(out,"w").write("\n".join(lines)+"\n")' "$LOG" "$REPORTS/fix_summary.md"
echo "Summary: $REPORTS/fix_summary.md"

# exit 0 if final state clean, else 2 if a residual remains. For --check route we
# judge ONLY route.json (a route fix never produces drc/lvs; a stale route.json
# from a prior flow must not poison a drc/lvs fix, and vice-versa).
#
# FAIL-CLOSED (2026-06-20 honesty fix): a check counts as signed off ONLY for the
# clean statuses below — mirroring engineer_loop._process_one's first-pass gate
# (status in {clean,clean_beol,skipped}). The prior fail-OPEN allowlist
# {fail,failed,residual,timeout} let every OTHER status pass as clean, so a DRC
# that timed out `stuck` (FEOL-hang) or an LVS that died `incomplete`/`crash`
# (no match verdict, no lvsdb) returned exit 0 -> the loop marked the design clean
# though signoff never verified. Any status outside clean_states is an unresolved
# residual. See references/failure-patterns.md + test_fix_signoff_clean_gate.py.
#
# A MISSING or UNREADABLE report for an ACTIVE check is itself a residual (rc=2),
# NOT "no residual" — mirroring engineer_loop._signoff_status, which defaults a
# missing/unreadable report to "unknown" (fail-closed). The earlier
# `if os.path.exists(p)` short-circuit let an absent report (e.g. an extract crash
# under fix_one's `|| true`) pass the gate, so for --check both a clean DRC alone
# could mark a design clean though LVS never verified (2026-06-23 audit, bug #4/#5).
python3 -c 'import json,sys,os
proj,check=sys.argv[1],sys.argv[2]; rc=0
# timing is its own check: it has no <check>.json with a `status` field — it judges
# reports/timing_check.json by `tier`. Timing is MET (exit 0) iff tier in {clean,
# minor} (minor = WNS<0 but auto-closeable; the loop already drove the relax). Any
# other tier (moderate/severe/unconstrained/unknown) or a missing/unreadable report
# is an unresolved residual (exit 2) — fail-closed, mirroring the drc/lvs gate.
if check=="timing":
    p=os.path.join(proj,"reports","timing_check.json"); tier=None
    if os.path.exists(p):
        try: tier=json.load(open(p)).get("tier")
        except Exception: tier=None
    sys.exit(0 if tier in {"clean","minor"} else 2)
# Judge ONLY the report(s) for the REQUESTED check — a stale report from a different
# check must not poison this one (and --check drc must not require an absent lvs.json).
checks={"route":("route",),"drc":("drc",),"lvs":("lvs",)}.get(check,("drc","lvs"))
clean_states={"clean","clean_beol","skipped"}
for c in checks:
    p=os.path.join(proj,"reports",c+".json")
    st=None
    if os.path.exists(p):
        try: st=json.load(open(p)).get("status")
        except Exception: st=None
    if st not in clean_states: rc=2
sys.exit(rc)' "$PROJECT_DIR" "$CHECK" || exit 2
