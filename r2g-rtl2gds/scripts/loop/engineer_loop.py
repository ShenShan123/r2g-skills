#!/usr/bin/env python3
"""Engineer-loop campaign orchestrator (spec §5.1, §6). Deterministic core:
pull design -> flow -> signoff -> fix -> ingest -> learn -> recipe diff ->
A/B arms (as ordinary ledger entries) -> verdict -> promote/demote. Unknowns
go to the escalations queue; the loop NEVER blocks on them.

Usage:
  engineer_loop.py run --ledger design_cases/_loop/ledger.jsonl [--max N]
  engineer_loop.py add --ledger L --project <dir> [--platform nangate45]
  engineer_loop.py status --ledger L

Hard rules honored: unique FLOW_VARIANT per project dir (run_orfs derives it
from the basename — A/B arms copy to <design>_ab{A,B}_<strategy8> dirs);
single LVS at a time (workers=1 in Phase 1); PLACE_DENSITY clamps live in
diagnose/suggest and are never touched here.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = SKILL_ROOT / "knowledge"
FLOW = SKILL_ROOT / "scripts" / "flow"
REPORTS = SKILL_ROOT / "scripts" / "reports"
sys.path.insert(0, str(KNOWLEDGE))
# scripts/reports/ is needed in PRODUCTION for `import fmax_model` in the Fmax pre-pass.
# conftest.py injects this under pytest, which previously MASKED its absence here — the
# fmax-drain SDC stamp was silently inert off-test (2026-06-24 review L4-01, the same
# fixture!=production class as the 22f3e67 fmax pilot bug). Set it at module load.
sys.path.insert(0, str(REPORTS))

STATES = ("pending", "flow", "signoff", "fixing", "clean", "escalated",
          "abandoned")


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


class Ledger:
    """JSONL event log; last state per design wins. Append-only -> resumable."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, dict] = {}
        # Guards _entries + the JSONL append so parallel A/B arm workers
        # (R2G_AB_WORKERS > 1) can update the ledger concurrently without
        # interleaving lines or racing the dict (2026-06-17 parallel ab_drain).
        self._lock = threading.Lock()
        if self.path.exists():
            for ln in self.path.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                e = json.loads(ln)
                cur = self._entries.setdefault(e["design"], {})
                cur.update(e)
                # A 'pending' event is fresh work (e.g. a re-planned A/B arm): drop any
                # stale 'judged' carried from a PRIOR wave so judge_finished_trials (which
                # filters `not judged`) RE-judges the re-run. Without this, an A/B candidate
                # whose arm dirs survive a prior wave re-runs every wave but its new verdict
                # is NEVER recorded -> it can never promote and _ab_coverage_gap is starved
                # of the trials it counts (2026-06-27 audit; the large-pin place class).
                if e.get("state") == "pending":
                    cur.pop("judged", None)

    def _append(self, obj: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, sort_keys=True) + "\n")

    def add(self, entry: dict) -> None:
        e = dict(entry)
        e.setdefault("kind", "normal")
        e.setdefault("state", "pending")
        e["ts"] = _now()
        with self._lock:
            cur = self._entries.setdefault(e["design"], {})
            cur.update(e)
            # Re-planning an arm whose dir survived a prior wave resets it to 'pending' so it
            # RE-RUNS; drop the prior wave's stale 'judged' so its new verdict is re-recorded
            # (mirrors __init__'s reload invariant; 2026-06-27).
            if e.get("state") == "pending":
                cur.pop("judged", None)
            self._append(e)

    def set_state(self, design: str, state: str, **extra) -> None:
        if state not in STATES:
            raise ValueError(f"illegal state: {state}")
        e = {"design": design, "state": state, "ts": _now(), **extra}
        with self._lock:
            self._entries[design].update(e)
            self._append(e)

    def state(self, design: str) -> str:
        return self._entries[design]["state"]

    def entries(self) -> list[dict]:
        return list(self._entries.values())

    def pending(self) -> list[dict]:
        return [e for e in self._entries.values() if e["state"] == "pending"]


# ---- subprocess seams (monkeypatched in tests; env-overridable like
# fix_signoff's R2G_RUN_ORFS) -------------------------------------------------

def _script(env_key: str, default: Path) -> str:
    return os.environ.get(env_key, str(default))


def _run_flow(entry: dict) -> int:
    return subprocess.run(
        ["bash", _script("R2G_LOOP_RUN_FLOW", FLOW / "run_orfs.sh"),
         entry["project_path"], entry["platform"]]).returncode


# Recipe strategy classes the inline A/B harness drives through a DEDICATED divergent
# arm runner instead of the DRC/LVS signoff path. Keyed by STRATEGY (not symptom)
# because the timing symptom_ids are not always present in the symptoms table
# (period_relax's 913f3c.../c9aba8... are absent), so a symptom-only lookup mis-routes
# them to 'both' -> identical inert arms that can never promote and burn a full
# multi-hour signoff per repeat (2026-06-24 audit, bugs #1/#3).
_PLACE_STRATEGIES = frozenset({"core_util_relief"})
_TIMING_STRATEGIES = frozenset({"period_relax", "utilization_reduce",
                                "backend_aware_synth_retune"})
# synth_memory_relax is a SYNTH backend-abort recovery (raise SYNTH_MEMORY_MAX_BITS +
# pair a die auto-size): its A/B arm applies the recipe up-front and flows once, like the
# place/route backend-abort arms, and is judged on 'synth cleared' (2026-06-28).
_SYNTH_STRATEGIES = frozenset({"synth_memory_relax"})
# Strategies whose A/B arms CANNOT diverge (no real edit applied) — never plan a trial
# for them (it can only ever be inconclusive). lvs_resolve_unknown re-inspects with
# config_edits={} (a no-op), so arm A and arm B do byte-identical work.
_NONDIVERGENT_STRATEGIES = frozenset({"lvs_resolve_unknown"})
# A candidate that accrues this many inconclusive trials with ZERO decisive verdicts is
# not learnable from the available subjects/harness — stop re-planning it (bug #1)
# WITHOUT demoting it (bug #2: inconclusive is non-terminal); surface it once instead.
AB_INCONCLUSIVE_MAX = 3


def _symptom_check(conn, symptom_id: str | None, strategy: str | None = None) -> str:
    """Map a candidate recipe to the fix-loop --check value that makes its A/B arms do
    DIFFERENT work. Route by STRATEGY first (robust to a missing symptoms row): a place
    recipe -> 'place' (apply-then-flow; FLW-0024 die-resize is a backend abort like
    route), a timing recipe -> 'timing' (fix_signoff --check timing reflow). Then fall
    back to the symptom table: a route/place backend abort (check=orfs_stage) -> that
    stage; everything else (DRC/LVS/antenna/density) -> 'both' (the DRC/LVS signoff
    fixer, where R2G_FIX_EXCLUDE/RANK_FIRST already diverge the arms)."""
    if strategy in _PLACE_STRATEGIES:
        return "place"
    if strategy in _TIMING_STRATEGIES:
        return "timing"
    if strategy in _SYNTH_STRATEGIES:
        return "synth"
    if not symptom_id:
        return "both"
    row = conn.execute(
        "SELECT check_type, class FROM symptoms WHERE symptom_id=?",
        (symptom_id,)).fetchone() if conn is not None else None
    if row and row[0] == "orfs_stage":
        if row[1] == "route":
            return "route"
        if row[1] == "place":
            return "place"
        if row[1] == "synth":
            return "synth"
    return "both"


def _run_fix(entry: dict) -> int:
    env = dict(os.environ)
    if entry.get("kind") == "ab_arm":
        if entry.get("arm") == "A":
            env["R2G_FIX_EXCLUDE"] = entry["strategy"]
        else:
            env["R2G_FIX_RANK_FIRST"] = entry["strategy"]
    return subprocess.run(
        ["bash", _script("R2G_LOOP_FIX", FLOW / "fix_signoff.sh"),
         entry["project_path"], entry["platform"], "--check",
         entry.get("check", "both")],
        env=env).returncode


def _apply_recipe_strategy(entry: dict) -> None:
    """Apply the recipe's backend strategy into the arm's config.mk BEFORE its single
    flow run (arm B of an apply-then-flow backend-abort trial).

    - PLACE (core_util_relief): two sub-cases. A FIXED-die subject (DIE_AREA, no
      CORE_UTILIZATION) is converted DIE_AREA -> CORE_UTILIZATION=30 so ORFS auto-sizes a
      die that FITS the cells (the FLW-0024 recovery). A subject that ALREADY auto-sizes
      (CORE_UTILIZATION=N) gets its util LOWERED (more whitespace -> easier place/route).
      Either way arm B's place stage diverges from arm A's (control) untouched config.
      Direct edit — core_util_relief is NOT a diagnose strategy (2026-06-24 audit, bug
      #3-place; the already-auto-sized lowering was the no-op fixed 2026-06-26).
    - ROUTE (route_relief / route strategies): seed a fail route.json so diagnose can
      resolve the route strategy (no backend exists yet to extract from), then apply it.
    """
    if entry.get("strategy") in _SYNTH_STRATEGIES:
        # SYNTH backend abort: apply the SAME recovery as process_one's in-loop fix -- raise
        # SYNTH_MEMORY_MAX_BITS AND pair an auto-sized low-util die so the FF-expanded design
        # clears synth AND places. Arm A (control, no recipe) memcap-aborts at synth; arm B
        # diverges by getting PAST synth (judged on 'synth cleared', not full signoff).
        _raise_synth_memory_cap(entry)
        _resize_to_core_util(entry, util=_SYNTH_MEM_CORE_UTIL)
        return
    if entry.get("strategy") in _PLACE_STRATEGIES:
        # A PPL-0024 (pin-overflow) subject needs a PERIMETER-targeted die, not the cell-area
        # util lever -- a fixed 0.6x util step undershoots cell-tiny/pin-huge designs so arm B
        # PPL-0024-aborts just like arm A and the trial ties inconclusive forever (2026-06-27).
        # The arm copy excludes the subject's backend, so the required perimeter is passed in
        # from the SUBJECT at plan time (pin_perimeter_target); when present, hit it directly.
        tgt = entry.get("pin_perimeter_target")
        if tgt and _relieve_pin_overflow(entry, perimeter_target=tgt):
            return
        # FLW-0024 / generic place relief: a fixed-die subject -> CORE_UTILIZATION=30 (the
        # FLW-0024 recovery). A subject that already auto-sizes makes _resize_to_core_util a
        # no-op; there relief = LOWER the existing util so arm B diverges from the arm-A
        # control (the no-op that stalled the place class -- both arms util=20; 2026-06-26).
        if not _resize_to_core_util(entry):
            _lower_core_util(entry)
        return
    proj = Path(entry["project_path"])
    reports = proj / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "route.json").write_text(
        json.dumps({"status": "fail", "total_violations": None}), encoding="utf-8")
    diagnose = _script("R2G_LOOP_DIAGNOSE",
                       SKILL_ROOT / "scripts" / "reports" / "diagnose_signoff_fix.py")
    subprocess.run([sys.executable, diagnose, entry["project_path"],
                    "--check", "route", "--apply", entry["strategy"]], check=False)


def _process_backend_ab_arm(led: "Ledger", entry: dict, conn) -> None:
    """A/B arm for a BACKEND-ABORT symptom (orfs_stage/route OR orfs_stage/place).
    Unlike a signoff arm (flow succeeds -> signoff fails -> fix), a backend-abort arm's
    'fix' IS a config retune that lets a previously-aborting stage complete. So we apply
    the strategy up-front on arm B and run the flow EXACTLY ONCE per arm:
      - ROUTE: arm A control (default util -> route times out -> is_success False);
        arm B route_relief (lower util -> route completes -> True).
      - PLACE: arm A control (FLW-0024 die too small -> place aborts -> False);
        arm B core_util_relief (DIE_AREA->CORE_UTILIZATION -> place completes -> True).
    judge -> win. One flow per arm (no wasted control-config run on arm B)."""
    design = entry["design"]
    check = entry.get("check", "route")
    if entry.get("arm") == "B":
        led.set_state(design, "fixing")
        _apply_recipe_strategy(entry)
    led.set_state(design, "flow")
    rc = _run_flow(entry)
    if not _has_backend_run(entry):
        # The arm flow produced no backend at all (clone/setup aborted before any
        # stage ran): do NOT ingest a junk orfs_status='unknown' row; escalate so
        # the dropped arm is visible and judge_finished_trials records no false
        # verdict for the trial (2026-06-23 audit, bug #3).
        led.set_state(design, "escalated", reason=f"{check}_arm_incomplete")
        return
    _ingest(entry)
    # The judge reads the ingested run's is_success; rc only drives the ledger
    # terminal state (clean vs escalated) so judge_finished_trials picks it up.
    led.set_state(design, "clean" if rc == 0 else "escalated",
                  **({} if rc == 0 else {"reason": f"{check}_arm_failed"}))


def _journal_ab_launch(entry: dict) -> None:
    """Best-effort Tier-B1 journal of an A/B arm launch. Per-arm — may run in a
    worker thread (R2G_AB_WORKERS) — so it opens its OWN WAL journal conn (the
    journal is WAL + busy_timeout; never a knowledge-side write from a thread).
    ADVISORY only; honors R2G_JOURNAL. The arm's symptom_id comes from its ab_key."""
    if os.environ.get("R2G_JOURNAL", "1") == "0":
        return
    try:
        import journal_db
        key = entry.get("ab_key") or {}
        conn = journal_db.connect(
            os.environ.get("R2G_JOURNAL_DB") or journal_db.DEFAULT_JOURNAL_PATH)
        journal_db.ensure_schema(conn)
        journal_db.append_action(
            conn, project_path=entry.get("project_path", ""), actor="loop",
            action_type="ab_launch", design=entry.get("design"),
            platform=entry.get("platform"), symptom_id=key.get("symptom_id"),
            payload={"arm": entry.get("arm"), "strategy": entry.get("strategy"),
                     "symptom_id": key.get("symptom_id"),
                     "repeat": entry.get("repeat"), "check": entry.get("check"),
                     "match_level": entry.get("match_level")})
        conn.close()
    except Exception:                          # telemetry must never break the arm
        pass


def _has_backend_run(entry: dict) -> bool:
    """True iff the project produced at least one backend stage_log — i.e. a flow
    actually RAN (even if it failed mid-stage). Distinguishes a genuine (possibly
    partial) flow result, which MUST be ingested, from an arm/clone that aborted
    before any stage ran (which would otherwise ingest as a junk orfs_status=
    'unknown' row). (2026-06-23 audit, bug #3.)"""
    proj = Path(entry["project_path"])
    return any(proj.glob("backend/RUN_*/stage_log.jsonl"))


def _ingest(entry: dict) -> str | None:
    # Skip a project that produced NO flow result at all — no backend stage_log AND
    # no ppa.json. Ingesting it writes a junk orfs_status='unknown' run row
    # (DESIGN_NAME defaults to 'unknown', platform to 'nangate45') that (a) pollutes
    # the corpus and (b) via _arm_metric's latest-row-per-project query can clobber a
    # prior real arm outcome and turn an A/B trial into a FALSE loss (2026-06-23
    # audit, bug #3). A genuine PARTIAL run has a stage_log and is still ingested
    # (honesty: ingest after every real flow — clean, failed, or partial).
    proj = Path(entry["project_path"])
    if not _has_backend_run(entry) and not (proj / "reports" / "ppa.json").exists():
        return None
    r = subprocess.run(
        [sys.executable, _script("R2G_LOOP_INGEST", KNOWLEDGE / "ingest_run.py"),
         entry["project_path"]], capture_output=True, text=True)
    for tok in (r.stdout or "").split():
        if tok.startswith("run_id="):
            return tok.split("=", 1)[1]
    return None


def _fail_stage(entry: dict) -> str | None:
    """The backend stage that aborted in the newest run's stage_log (its LAST
    line — run_orfs.sh stops at the first failing stage), or None. Lets the loop
    distinguish a KNOWN, recipe-backed backend-abort (route congestion / DRT
    timeout) from a genuinely unhandled crash, so it can fix the former in-loop
    instead of escalating it."""
    proj = Path(entry["project_path"])
    logs = sorted(proj.glob("backend/RUN_*/stage_log.jsonl"))
    if not logs:
        return None
    try:
        rows = [json.loads(ln) for ln in logs[-1].read_text().splitlines() if ln.strip()]
    except Exception:
        return None
    if not rows:
        return None
    last = rows[-1]
    status = last.get("status")
    if status not in (0, "0", "pass"):
        return last.get("stage")
    return None


def _is_flw0024(entry: dict) -> bool:
    """True if the newest backend run aborted with FLW-0024 (place density > 1.0):
    the die is too small to hold the synthesized cells -- a RECOVERABLE over-pack
    (the project's fixed DIE_AREA was sized from an RTL line-count proxy, not gate
    count, so a compact-but-dense design over-packs), NOT the irrecoverable
    NesterovSolve placement divergence. Read from the run's flow.log. (2026-06-23)"""
    proj = Path(entry["project_path"])
    logs = sorted(proj.glob("backend/RUN_*/flow.log"))
    if not logs:
        return False
    try:
        return "FLW-0024" in logs[-1].read_text(errors="ignore")
    except OSError:
        return False


def _is_ppl0024(entry: dict) -> bool:
    """True if the newest backend run aborted with PPL-0024 (IO pins exceed the die's
    available perimeter pin positions): the die PERIMETER is too small for the design's
    pin count -- recoverable by ENLARGING the die (a bigger core has a longer perimeter
    with more pin slots), DISTINCT from FLW-0024 (place density / die too small for the
    CELLS). Read from the run's flow.log. (2026-06-26 audit: ~35 PPL-0024 place aborts
    were mislabeled 'unseen_crash' because the loop had no pin-aware die handler.)"""
    proj = Path(entry["project_path"])
    logs = sorted(proj.glob("backend/RUN_*/flow.log"))
    if not logs:
        return False
    try:
        return "PPL-0024" in logs[-1].read_text(errors="ignore")
    except OSError:
        return False


def _config_knob(line: str) -> str:
    """'export DIE_AREA  = 0 0 50 50' -> 'DIE_AREA' (no regex dependency)."""
    parts = line.split()
    if len(parts) >= 2 and parts[0] == "export":
        return parts[1].split("=")[0].strip()
    return ""


def _resize_to_core_util(entry: dict, util: int = 30) -> bool:
    """FLW-0024 recovery: rewrite constraints/config.mk from a fixed DIE_AREA/
    CORE_AREA to CORE_UTILIZATION so ORFS auto-sizes a die that FITS the cells.
    Never touches PLACE_DENSITY_LB_ADDON (the hard-rule floor). Returns True iff it
    changed the file; a no-op (False) when there is no config.mk, no DIE_AREA to
    convert, or CORE_UTILIZATION is already set (already auto-sized -> nothing to
    relax, so a retry would be pointless)."""
    cfg = Path(entry["project_path"]) / "constraints" / "config.mk"
    if not cfg.is_file():
        return False
    lines = cfg.read_text().splitlines()
    if any(_config_knob(ln) == "CORE_UTILIZATION" for ln in lines):
        return False
    kept, had_area = [], False
    for ln in lines:
        if _config_knob(ln) in ("DIE_AREA", "CORE_AREA"):
            had_area = True
            continue
        kept.append(ln)
    if not had_area:
        return False
    kept.append(f"export CORE_UTILIZATION = {util}")
    cfg.write_text("\n".join(kept) + "\n")
    return True


# core_util_relief on a subject that ALREADY auto-sizes: how far arm B lowers util.
_CORE_UTIL_RELIEF_FACTOR = 0.6   # lower an existing CORE_UTILIZATION=N to ~60% of N
_CORE_UTIL_FLOOR = 10            # never below 10% (the die is already huge; lower is moot)


def _lower_core_util(entry: dict, *, factor: float = _CORE_UTIL_RELIEF_FACTOR,
                     floor: int = _CORE_UTIL_FLOOR) -> bool:
    """core_util_relief on a subject that ALREADY auto-sizes (config has CORE_UTILIZATION
    =N): LOWER N (more whitespace -> easier place/route) so arm B genuinely diverges from
    the arm-A control. _resize_to_core_util only converts a FIXED DIE_AREA and no-ops when
    CORE_UTILIZATION is already present -- which left every core_util_relief A/B arm
    byte-identical to its control (both util=20) -> inconclusive forever -> the place class
    never promoted and the loop stalled (2026-06-26 audit; the place A/B arm was deferred
    at the 2026-06-24 review, see _record_resize_fix scope note). Returns True iff it
    lowered the value; False when there is no CORE_UTILIZATION line or it is already at/
    below the floor (genuinely no relief left -> an honest non-divergent inconclusive)."""
    cfg = Path(entry["project_path"]) / "constraints" / "config.mk"
    if not cfg.is_file():
        return False
    out, changed = [], False
    for ln in cfg.read_text().splitlines():
        if _config_knob(ln) == "CORE_UTILIZATION":
            try:
                cur = float(ln.split("=", 1)[1].strip())
            except (IndexError, ValueError):
                out.append(ln)
                continue
            new = max(floor, int(round(cur * factor)))
            if new < cur:
                out.append(f"export CORE_UTILIZATION = {new}")
                changed = True
                continue
        out.append(ln)
    if changed:
        cfg.write_text("\n".join(out) + "\n")
    return changed


_PIN_RELIEF_UTIL = 15   # PPL-0024 FALLBACK: convert a fixed die to this (low) util -> big die
_PIN_PERIMETER_MARGIN = 1.15   # size the CORE ~15% past the placer's stated requirement
_PIN_CORE_INSET_UM = 10        # core-to-die margin (um) for the IO ring


def _ppl0024_required_perimeter(project_path: str) -> float | None:
    """The die perimeter (um) the IO placer DEMANDS, parsed from the newest backend run's
    PPL-0024 message ('... Increase the die perimeter from <A>um to <B>um.'). Returns <B>
    (the REQUIRED perimeter) or None when there is no PPL-0024 / it is unparseable.

    This is the placer's OWN stated target -- the only lever that actually closes a
    pin-overflow abort, because CORE_UTILIZATION sizes the die from CELL AREA, not pin
    perimeter: a fixed fractional util step undershoots a cell-tiny/pin-huge design (a
    0.6x step grew ip_demux's perimeter 490->631um where the placer demanded 851.76um),
    so BOTH A/B arms PPL-0024-abort identically -> the trial ties inconclusive forever and
    no nangate45 recipe ever promotes (2026-06-27 audit)."""
    proj = Path(project_path)
    logs = sorted(proj.glob("backend/RUN_*/flow.log"))
    if not logs:
        return None
    try:
        txt = logs[-1].read_text(errors="ignore")
    except OSError:
        return None
    m = re.search(r"die perimeter from [\d.]+\s*um to ([\d.]+)\s*um", txt)
    return float(m.group(1)) if m else None


def _set_explicit_die(project_path: str, perimeter_um: float | None) -> bool:
    """Rewrite constraints/config.mk to a SQUARE DIE_AREA/CORE_AREA whose CORE perimeter
    MEETS `perimeter_um` (x _PIN_PERIMETER_MARGIN), dropping any CORE_UTILIZATION/DIE_AREA/
    CORE_AREA so the IO placer gets exactly the perimeter it demanded. The perimeter-targeted
    inverse of _resize_to_core_util (which targets cell area). Never touches
    PLACE_DENSITY_LB_ADDON (the hard-rule floor). Returns True iff it changed the file; a
    no-op (False) when there is no config.mk or no positive perimeter to hit."""
    cfg = Path(project_path) / "constraints" / "config.mk"
    if not cfg.is_file() or not (perimeter_um and perimeter_um > 0):
        return False
    inset = _PIN_CORE_INSET_UM
    core_side = int(perimeter_um / 4.0 * _PIN_PERIMETER_MARGIN) + 1   # ceil -> core meets target
    die_side = core_side + 2 * inset
    keep = [ln for ln in cfg.read_text().splitlines()
            if _config_knob(ln) not in ("CORE_UTILIZATION", "DIE_AREA", "CORE_AREA")]
    keep.append(f"export DIE_AREA = 0 0 {die_side} {die_side}")
    keep.append(f"export CORE_AREA = {inset} {inset} {inset + core_side} {inset + core_side}")
    cfg.write_text("\n".join(keep) + "\n")
    return True


def _relieve_pin_overflow(entry: dict, *, perimeter_target: float | None = None) -> bool:
    """PPL-0024 recovery: ENLARGE the die so its perimeter exposes more IO-pin positions.
    PREFERRED lever: size an explicit DIE_AREA/CORE_AREA to MEET the perimeter the placer
    demanded -- parsed from this run's own PPL-0024 message (process_one, where the subject
    aborted in place), or passed as `perimeter_target` from the A/B-arm SUBJECT (whose backend
    carried the message; the arm copy excludes it). The cell-area CORE_UTILIZATION lever is
    only a FALLBACK for a subject with no parseable perimeter (e.g. an FLW-0024 over-pack):
    it undershoots cell-tiny/pin-huge designs, the exact tie that stalled nangate45 promotion
    (2026-06-27 audit). Returns True iff it changed the config."""
    target = perimeter_target or _ppl0024_required_perimeter(entry["project_path"])
    if _set_explicit_die(entry["project_path"], target):
        return True
    if _lower_core_util(entry):
        return True
    return _resize_to_core_util(entry, util=_PIN_RELIEF_UTIL)


def _record_resize_fix(entry: dict, *, cleared: bool) -> None:
    """Record the FLW-0024 die-resize (DIE_AREA -> CORE_UTILIZATION) as a fix_log row
    so the NEXT _ingest projects it into fix_events -> fix_trajectories -> a Tier-3
    recipe — making the recovery VISIBLE to learning (honest accounting, a
    cross-design CORE_UTILIZATION prior, and fix-history subjects for the place
    symptom). Before this the resize left ZERO learning trace (2026-06-23 audit, bug
    #6).

    SCOPE (2026-06-23 review): this makes the resize LEARNABLE, not A/B-PROMOTABLE.
    The resize is a sticky, one-shot config change applied UNCONDITIONALLY in
    process_one (not selected via diagnose, and strategy 'core_util_relief' is not a
    diagnose --check strategy), and its A/B control is hard to reconstruct once the
    die is auto-sized — so the place-resize recipe is NOT driven through the
    candidate->A/B->promoted lifecycle, and promotion is moot while the recovery is
    hard-coded. Wiring a backend-abort A/B arm for class=place is deferred alongside
    #9b (see docs/superpowers/plans/r2g-loop-closure-audit-2026-06-23.md).

    Keyed check='orfs_stage' / class='place' (no predicates) so symptom.from_fix_log_row
    lands it under the place symptom af17c0ba7f62c48e — the symptom_id is computed at
    INGEST from these fields, so there is no separate-writer / symptom_id drift. The
    outcome is honest: clean retry -> cleared (before=1, after=0); a retry that still
    aborts -> no_change (before=after=1), preserving negative learning."""
    proj = Path(entry["project_path"])
    reports = proj / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    runs = sorted(proj.glob("backend/RUN_*"))
    run_tag = runs[-1].name if runs else "norun"
    sid = "resize_" + hashlib.sha1(f"{proj}:{run_tag}".encode("utf-8")).hexdigest()[:12]
    row = {
        "fix_session_id": sid, "iter": 1, "strategy": "core_util_relief",
        "check": "orfs_stage", "violation_class": "place", "from_stage": "place",
        "before": 1, "after": 0 if cleared else 1,
        "before_status": "fail", "after_status": "clean" if cleared else "fail",
        "verdict": "cleared" if cleared else "no_change", "ts": _now(),
    }
    with (reports / "fix_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


# ── Synth-stage abort classification + memory-cap recovery ───────────────────
# An early synth abort (rc!=0 before any reports) is NOT a mystery: the Yosys log
# names the cause. The loop used to collapse all of them into 'unseen_crash', which
# hid a MECHANICAL, documented recovery (raise SYNTH_MEMORY_MAX_BITS) for 15 designs
# and mislabeled 58 deterministic conditions (missing `include header / synth timeout)
# as crashes -> the learner saw mysteries, not actionable signatures (2026-06-28
# unseen_crash audit; only ~6 of 79 were genuine downstream crashes). Mirrors the
# FLW-0024 / PPL-0024 detectors above (signature-keyed read of the newest flow.log).
_SYNTH_MEM_BITS_RETRY = 65536   # the skill's documented per-memory cap (SKILL.md:634);
                                # 16x the ORFS default 4096, enough for register files /
                                # FIFOs that just overflow it, bounded so a huge memory
                                # cannot explode into millions of flops.
_SYNTH_MEM_CORE_UTIL = 20       # FF-expanded memory bloats the design ~16x, so a fixed
                                # DIE_AREA over-packs at place; pair the cap raise with an
                                # auto-sized low-util die (failure-patterns.md:1163).


def _newest_flow_log(entry: dict) -> str:
    """Text of the newest backend run's flow.log, or '' (best-effort, never raises).
    Shared reader for the synth-abort detectors."""
    proj = Path(entry["project_path"])
    logs = sorted(proj.glob("backend/RUN_*/flow.log"))
    if not logs:
        return ""
    try:
        return logs[-1].read_text(errors="ignore")
    except OSError:
        return ""


def _is_synth_memory_cap(entry: dict) -> bool:
    """True if synth aborted because an inferred memory exceeds SYNTH_MEMORY_MAX_BITS
    (Yosys' 'Synthesized memory size N exceeds SYNTH_MEMORY_MAX_BITS'). RECOVERABLE:
    the ORFS default 4096 is too tight for real register files / FIFOs -- raise the cap
    and re-flow (SKILL.md:395, failure-patterns.md:1149)."""
    return "exceeds SYNTH_MEMORY_MAX_BITS" in _newest_flow_log(entry)


def _is_synth_missing_header(entry: dict) -> bool:
    """True if synth aborted on an unresolved `include (Yosys 'Can't open include
    file'): the harvested RTL never shipped the header -- genuinely INCOMPLETE upstream
    input, not a crash. setup_rtl_designs.py already flags these (metadata.json
    status=incomplete_missing_headers); they must escalate honestly, not as a mystery."""
    return "Can't open include file" in _newest_flow_log(entry)


def _is_synth_timeout(entry: dict) -> bool:
    """True if yosys synthesis hit the run_orfs.sh wrapper timeout (rc=124): the design
    is large enough that canonicalize/synth did not finish in the budget. Honest reason
    'synth_timeout' (the operator runbook can raise ORFS_TIMEOUT or simplify), never a
    mystery crash."""
    txt = _newest_flow_log(entry)
    return ("exit code 124" in txt and "synth" in txt) or "do-yosys-canonicalize] Terminated" in txt


def _raise_synth_memory_cap(entry: dict, bits: int = _SYNTH_MEM_BITS_RETRY) -> bool:
    """Memory-cap recovery: set/raise SYNTH_MEMORY_MAX_BITS in constraints/config.mk so
    Yosys infers the (now too-large-for-4096) memory into flip-flops instead of refusing.
    Replaces an existing lower value, appends when absent. Returns True iff it raised the
    cap; a no-op (False) when there is no config.mk, or the cap is already >= `bits`
    (already raised as far as the loop will go -> a retry would be pointless -> let it
    escalate as synth_memory_residual)."""
    cfg = Path(entry["project_path"]) / "constraints" / "config.mk"
    if not cfg.is_file():
        return False
    out, found, raised = [], False, False
    for ln in cfg.read_text().splitlines():
        if _config_knob(ln) == "SYNTH_MEMORY_MAX_BITS":
            found = True
            try:
                cur = int(ln.split("=", 1)[1].strip())
            except (IndexError, ValueError):
                cur = 0
            if cur < bits:
                out.append(f"export SYNTH_MEMORY_MAX_BITS = {bits}")
                raised = True
                continue
            out.append(ln)                       # already >= target -> keep, no-op
            continue
        out.append(ln)
    if not found:
        out.append(f"export SYNTH_MEMORY_MAX_BITS = {bits}")
        raised = True
    if raised:
        cfg.write_text("\n".join(out) + "\n")
    return raised


def _record_synth_mem_fix(entry: dict, *, cleared: bool) -> None:
    """Record the SYNTH_MEMORY_MAX_BITS raise as a fix_log row so the next _ingest
    projects it into fix_events -> a Tier-3 'synth_memory_relax' recipe -- the synth
    memory-cap recovery becomes VISIBLE to learning (a cross-design prior keyed to the
    synth symptom), exactly as _record_resize_fix does for the place die-resize. check=
    'orfs_stage' / class='synth' so symptom.from_fix_log_row keys it under the synth
    abort symptom. Honest outcome: clean retry -> cleared; a retry that still aborts ->
    no_change (negative learning)."""
    proj = Path(entry["project_path"])
    reports = proj / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    runs = sorted(proj.glob("backend/RUN_*"))
    run_tag = runs[-1].name if runs else "norun"
    sid = "synthmem_" + hashlib.sha1(f"{proj}:{run_tag}".encode("utf-8")).hexdigest()[:12]
    row = {
        "fix_session_id": sid, "iter": 1, "strategy": "synth_memory_relax",
        "check": "orfs_stage", "violation_class": "synth", "from_stage": "synth",
        "before": 1, "after": 0 if cleared else 1,
        "before_status": "fail", "after_status": "clean" if cleared else "fail",
        "verdict": "cleared" if cleared else "no_change", "ts": _now(),
    }
    with (reports / "fix_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _signoff_status(entry: dict) -> dict:
    out = {}
    for check in ("drc", "lvs"):
        p = Path(entry["project_path"]) / "reports" / f"{check}.json"
        try:
            out[check] = json.loads(p.read_text()).get("status", "unknown")
        except Exception:
            out[check] = "unknown"
    return out


def _learn() -> dict:
    import learn_heuristics
    import knowledge_db
    return learn_heuristics.learn(knowledge_db.DEFAULT_DB_PATH,
                                  KNOWLEDGE / "heuristics.json")


# ---- the loop ---------------------------------------------------------------

def _mark_clean(led: Ledger, conn, design: str, note: str) -> None:
    """Transition a design to `clean` AND auto-close any open escalations for it.
    A later successful flow/fix supersedes an earlier abort, so its escalation must
    not linger in the queue as a stale "still stuck" entry (2026-06-17)."""
    led.set_state(design, "clean")
    if conn is not None:
        try:
            import escalations
            n = escalations.resolve_for_design(conn, design, notes=note)
            if n:
                log_msg = f"[loop] {design}: clean -> auto-drained {n} stale escalation(s)"
                print(log_msg)
        except Exception:                       # reconciliation must never break the loop
            pass


def process_one(led: Ledger, entry: dict, conn, *,
                _resized: bool = False) -> str | None:
    """Run one design end-to-end. Returns the TERMINAL status it reached —
    'clean' | 'escalated' (or None for an A/B arm handled out-of-band) — so a
    caller (e.g. the FLW-0024 resize retry) can record the honest outcome without
    re-deriving it from the ledger (2026-06-23 audit, bug #6)."""
    design = entry["design"]
    if entry.get("kind") == "ab_arm":
        _journal_ab_launch(entry)           # Tier B1 — advisory decision telemetry
    # Backend-abort A/B arm (route congestion OR place FLW-0024 die-too-small OR synth
    # memcap): the flow itself fails at the backend stage, so the signoff "flow -> fix"
    # model does not apply — route it through the dedicated apply-then-flow arm runner. A
    # TIMING arm (check='timing') instead reaches a completed flow whose timing MISSES, so
    # it falls through to the normal signoff path below and is fixed via fix_signoff
    # --check timing (2026-06-17 route-relief; 2026-06-24 place + timing close; 2026-06-28
    # synth memcap so synth_memory_relax can promote).
    if entry.get("kind") == "ab_arm" and entry.get("check") in ("route", "place", "synth"):
        _process_backend_ab_arm(led, entry, conn)
        return None
    led.set_state(design, "flow")
    rc = _run_flow(entry)
    if rc != 0:
        # Backend abort. Ingest first (partial runs still teach + record the fail
        # stage), then — if this is a KNOWN, recipe-backed backend-abort (route
        # congestion / DRT timeout) — let the loop FIX it in-loop (apply the
        # learned route_relief + reflow) instead of escalating it as an unseen
        # crash. The user directive: always run the loop's fixer on a failure case
        # rather than hand-fixing (2026-06-17). Only genuinely unhandled aborts
        # (synth/place/cts crashes, or a route fix that still fails) escalate.
        _ingest(entry)                      # partial runs still teach
        # FLW-0024 (place density > 1.0): the die is too small for the synthesized
        # cells -- a RECOVERABLE over-pack (the fixed DIE_AREA was sized from an RTL
        # line-count proxy, not gate count), NOT the irrecoverable NesterovSolve
        # divergence. Auto-size the die (DIE_AREA -> CORE_UTILIZATION) and retry the
        # flow ONCE; never touches PLACE_DENSITY_LB_ADDON. (2026-06-23)
        if (not _resized and entry.get("kind") != "ab_arm"
                and _fail_stage(entry) == "place" and _is_flw0024(entry)
                and _resize_to_core_util(entry)):
            led.set_state(design, "fixing")
            result = process_one(led, entry, conn, _resized=True)
            # Record the die-resize as a learnable fix attempt so the recovery is
            # not invisible to the loop (2026-06-23 audit, bug #6). The retry's
            # returned terminal status is the honest outcome (clean -> the resize
            # cleared the abort); re-ingest projects the appended fix_log row into a
            # fix_event (idempotent UPSERT on the retry's run row — same ppa.json
            # mtime -> same run_id).
            _record_resize_fix(entry, cleared=(result == "clean"))
            _ingest(entry)
            return result
        # PPL-0024 (IO pins exceed die perimeter): the die is too small in PERIMETER for
        # the design's pin count -- recover by ENLARGING the die (lower CORE_UTILIZATION ->
        # bigger core -> more perimeter pin slots), the same core_util_relief lever applied
        # for the pin cause. This was the DOMINANT mislabeled-'unseen_crash' class (2026-06-26
        # audit: ~35 designs). Retry the flow ONCE; the resize is recorded as a learnable fix.
        if (not _resized and entry.get("kind") != "ab_arm"
                and _fail_stage(entry) == "place" and _is_ppl0024(entry)
                and _relieve_pin_overflow(entry)):
            led.set_state(design, "fixing")
            result = process_one(led, entry, conn, _resized=True)
            _record_resize_fix(entry, cleared=(result == "clean"))
            _ingest(entry)
            return result
        # Synth memory-cap (Yosys refuses to infer a memory larger than the default
        # 4096-bit SYNTH_MEMORY_MAX_BITS): a RECOVERABLE synth abort -- raise the cap
        # and re-flow ONCE, recorded as a learnable fix. The remedy is documented
        # (SKILL.md:395, failure-patterns.md:1149) but the loop used to escalate it as
        # 'unseen_crash', hiding 15 mechanically-fixable designs and a learnable recipe
        # (2026-06-28 unseen_crash audit). Mirrors the FLW-0024 / PPL-0024 recoveries.
        if (not _resized and entry.get("kind") != "ab_arm"
                and _fail_stage(entry) == "synth" and _is_synth_memory_cap(entry)
                and _raise_synth_memory_cap(entry)):
            # Pair the cap raise with an auto-sized low-util die: the FF-expanded memory
            # bloats the design (a 4096->65536-bit RAM is ~16x the cells), so a fixed
            # DIE_AREA reliably over-packs at place after the cap raise (axis_fifo: 3072%
            # util -> FLW-0024). Converting a fixed DIE_AREA -> CORE_UTILIZATION=20 lets ORFS
            # size a die that FITS, so the SAME re-flow reaches signoff instead of clearing
            # synth only to abort at place (failure-patterns.md:1163; no-op if already
            # auto-sized -- the FF memory then just grows the existing die; 2026-06-28 pilot).
            _resize_to_core_util(entry, util=_SYNTH_MEM_CORE_UTIL)
            led.set_state(design, "fixing")
            result = process_one(led, entry, conn, _resized=True)
            # The synth fix's verdict is whether the SYNTH abort cleared (the re-flow got
            # PAST synth), NOT whether the whole flow reached clean. Raising the cap expands
            # the RAM to flip-flops, so a memcap design can clear synth yet over-pack at
            # place -- tying 'cleared' to result=='clean' would record that downstream place
            # failure as the synth fix FAILING (false negative learning that teaches the loop
            # synth_memory_relax does not work when it does). _fail_stage reflects the retry.
            synth_cleared = _fail_stage(entry) != "synth"
            _record_synth_mem_fix(entry, cleared=synth_cleared)
            _ingest(entry)
            return result
        reason, notes = "unseen_crash", f"run_orfs rc={rc}"
        if entry.get("kind") != "ab_arm" and _fail_stage(entry) == "route":
            led.set_state(design, "fixing")
            fix_rc = _run_fix({**entry, "check": "route"})
            _ingest(entry)
            if fix_rc == 0:
                _mark_clean(led, conn, design, "route_relief cleared the abort in-loop")
                return "clean"
            # route_relief ran but did NOT clear the abort — a KNOWN, recipe-backed
            # backend residual (congestion past the CORE_UTILIZATION floor, or a
            # DIE_AREA-sized design with no util knob to relieve), NOT an "unseen
            # crash". Mislabeling it unseen_crash pollutes the escalation queue and
            # the learning signal (it reads as a novel symptom). Label it honestly so
            # the operator runbook can route it to the v2 DIE_AREA lever.
            reason = "route_congestion_residual"
            notes = (f"route abort (rc={rc}); route_relief exhausted or inapplicable "
                     f"(util at floor, or DIE_AREA-sized — no CORE_UTILIZATION knob)")
        elif (entry.get("kind") != "ab_arm" and _fail_stage(entry) == "place"
              and _is_flw0024(entry)):
            # FLW-0024 that survived the auto-resize retry (cells exceed even the
            # auto-sized routable die): an honest residual, NOT an unseen crash.
            reason = "place_density_residual"
            notes = (f"FLW-0024 place-density overflow (rc={rc}); auto-resize to "
                     f"CORE_UTILIZATION did not clear")
        elif (entry.get("kind") != "ab_arm" and _fail_stage(entry) == "place"
              and _is_ppl0024(entry)):
            # PPL-0024 that survived die enlargement (pin count exceeds even the enlarged
            # perimeter -- a genuinely pin-dense design): an honest residual, NOT an unseen
            # crash. A proper pin-aware floorplan (CORE_ASPECT_RATIO / explicit pad ring) is
            # the next lever; labeled honestly so the operator runbook can route it.
            reason = "pin_overflow_residual"
            notes = (f"PPL-0024 IO pins exceed die perimeter (rc={rc}); die enlargement "
                     f"(lower CORE_UTILIZATION) did not create enough pin positions")
        elif (entry.get("kind") != "ab_arm" and _fail_stage(entry) == "synth"
              and _is_synth_memory_cap(entry)):
            # memory-cap that survived the cap raise (cells exceed even 65536-bit, or the
            # cap was already there): an honest residual, NOT an unseen crash.
            reason = "synth_memory_residual"
            notes = (f"inferred memory exceeds SYNTH_MEMORY_MAX_BITS (rc={rc}); raising the "
                     f"cap to {_SYNTH_MEM_BITS_RETRY} did not clear it (use a RAM macro)")
        elif (entry.get("kind") != "ab_arm" and _fail_stage(entry) == "synth"
              and _is_synth_missing_header(entry)):
            # an unresolved `include -- the harvested RTL is INCOMPLETE (the header was
            # never shipped upstream), not a crash. Honest, distinct from a mystery so
            # the queue/learner are not told this is a novel synth symptom.
            reason = "incomplete_missing_header"
            notes = (f"synth abort (rc={rc}): unresolved `include -- harvested RTL is "
                     f"missing a header (incomplete upstream; needs source completion)")
        elif (entry.get("kind") != "ab_arm" and _fail_stage(entry) == "synth"
              and _is_synth_timeout(entry)):
            # yosys synthesis hit the run_orfs.sh wrapper timeout -- a large design, not a
            # crash. Honest reason routes it to the ORFS_TIMEOUT / simplification runbook.
            reason = "synth_timeout"
            notes = (f"yosys synthesis timed out (rc={rc}); design too large to canonicalize "
                     f"in the stage budget (raise ORFS_TIMEOUT or reduce SYNTH_MEMORY_MAX_BITS)")
        led.set_state(design, "escalated", reason=reason)
        if conn is not None:
            import escalations
            escalations.open_escalation(
                conn, design=design, project_path=entry["project_path"],
                run_id=None, reason=reason, notes=notes)
        return "escalated"
    led.set_state(design, "signoff")
    status = _signoff_status(entry)
    # A signoff A/B arm MUST always reach _run_fix so arm A's R2G_FIX_EXCLUDE and
    # arm B's R2G_FIX_RANK_FIRST actually diverge the two arms — never short-circuit
    # it to clean on an inherited (or genuinely-empty) verdict (2026-06-23 audit,
    # bug #1, defense-in-depth alongside the reports/-exclude copytree fix above).
    if (entry.get("kind") != "ab_arm"
            and all(v in ("clean", "clean_beol", "skipped") for v in status.values())):
        _ingest(entry)
        _mark_clean(led, conn, design, "signoff clean on first pass")
        return "clean"
    led.set_state(design, "fixing")
    fix_rc = _run_fix(entry)
    _ingest(entry)
    if fix_rc == 0:
        _mark_clean(led, conn, design, "signoff fix cleared residual")
        return "clean"
    # Record the POST-fix residual, NOT the pre-fix `status` snapshot. On a first signoff
    # pass `status` (line ~838) is read before any DRC/LVS ran, so it is usually
    # {drc:unknown,lvs:unknown}; recording it made 184 catalog_exhausted escalations all
    # read 'unknown,unknown' in the queue, hiding their genuinely diverse residuals
    # (80 drc=stuck / 67 lvs=fail / 29 both — 2026-06-28 audit). _run_fix has now run the
    # checks, so re-reading reflects WHAT the fixer could not clear: the honest residual.
    residual = _signoff_status(entry)
    led.set_state(design, "escalated", reason="catalog_exhausted")
    if conn is not None:
        import escalations
        escalations.open_escalation(
            conn, design=design, project_path=entry["project_path"],
            run_id=None, reason="catalog_exhausted",
            notes=json.dumps(residual, sort_keys=True))
    return "escalated"


def _localize_arm_sdc(dst: Path) -> None:
    """Repoint an A/B arm copy's config.mk SDC_FILE at its OWN constraints/constraint.sdc.
    The subject's config.mk pins SDC_FILE to an ABSOLUTE path in the ORIGINAL design dir,
    so without this the arm's flow (and any period_relax SDC edit) silently use the
    SUBJECT's SDC at the FAILING period — the relaxed clock has NO effect and a timing arm
    can never diverge (the 22f3e67 Fmax-pilot SDC-pinning bug, recurring in the A/B arm;
    2026-06-25). Mirrors fmax_search.clone_variant's repoint."""
    cfg = dst / "constraints" / "config.mk"
    sdc = (dst / "constraints" / "constraint.sdc").resolve()
    if not cfg.is_file() or not sdc.exists():
        return
    text = cfg.read_text(encoding="utf-8")
    new, n = re.subn(r"(?m)^(\s*(?:export\s+)?SDC_FILE\s*=).*$", rf"\g<1> {sdc}", text)
    if n == 0:
        new = text.rstrip("\n") + f"\nexport SDC_FILE = {sdc}\n"
    cfg.write_text(new, encoding="utf-8")


def _ab_coverage_gap(conn, key: dict) -> bool:
    """True if an A/B trial for this candidate cannot produce a decisive verdict and so
    must NOT be planned (2026-06-24 audit, bugs #1/#2). Two cases: (1) the strategy
    applies no real edit (_NONDIVERGENT_STRATEGIES) so its arms are byte-identical;
    (2) the candidate has already accrued >= AB_INCONCLUSIVE_MAX inconclusive trials
    with ZERO decisive (win/loss) verdicts — the harness cannot differentiate it on the
    available subjects, so re-planning only burns compute. Neither case demotes the
    recipe (inconclusive is non-terminal); the caller surfaces an escalation instead."""
    if key.get("strategy") in _NONDIVERGENT_STRATEGIES:
        return True
    if conn is None:
        return False
    try:
        rows = conn.execute(
            "SELECT verdict FROM ab_trials WHERE symptom_id=? AND design_class=? AND "
            "platform=? AND strategy=?",
            (key["symptom_id"], key["design_class"], key["platform"],
             key["strategy"])).fetchall()
    except Exception:
        return False
    incon = sum(1 for (v,) in rows if v == "inconclusive")
    decisive = sum(1 for (v,) in rows if v in ("win", "loss"))
    return decisive == 0 and incon >= AB_INCONCLUSIVE_MAX


def plan_arms_for_candidates(led: Ledger, conn, *, n_ab_designs: int = 2,
                             repeats: int | None = None) -> int:
    """For every pending candidate recipe, plan an A/B trial and append its arm
    entries to the ledger (the SAME loop — or ab_drain — executes them). Returns
    the number of arm entries appended. Idempotent on the arm dirs (skips a dst
    that already exists).

    Win 2: each arm side is replicated `repeats` times (default R2G_AB_REPEATS,
    k=2) so the verdict is taken over a lower-confidence bound, not a single lucky
    run. Repeat dirs are <design>_ab{arm}_{strat8}_{r}; they share the arm field
    so judge_finished_trials aggregates them per arm."""
    import ab_runner
    import recipe_lifecycle
    k = repeats if repeats is not None else ab_runner.ab_repeats()
    appended = 0
    for key in recipe_lifecycle.pending_candidates(conn):
        # Coverage guard (2026-06-24 audit, bugs #1/#2): never plan a trial whose arms
        # CANNOT diverge — a no-op strategy (lvs_resolve_unknown), or a candidate that has
        # already accrued AB_INCONCLUSIVE_MAX inconclusive trials with ZERO decisive
        # verdicts (the harness/subjects cannot differentiate it). Planning it only burns
        # a full multi-hour signoff per repeat for a guaranteed-inconclusive verdict (the
        # period_relax stall). Skip + surface an idempotent escalation; NEVER demote
        # (inconclusive is non-terminal — a later corpus change can make it learnable).
        if _ab_coverage_gap(conn, key):
            print(f"[loop] A/B candidate skipped (coverage gap): {key['strategy']} "
                  f"symptom={key['symptom_id']} {key['design_class']}/{key['platform']}")
            if conn is not None:
                try:
                    import escalations
                    escalations.open_escalation(
                        conn, design=f"recipe:{key['strategy']}:{key['symptom_id']}",
                        project_path="", run_id=None, reason="ab_coverage_gap",
                        symptom_id=key["symptom_id"],
                        notes=json.dumps(key, sort_keys=True))
                except Exception:               # surfacing must never break the drain
                    pass
            continue
        try:
            trial = ab_runner.plan_trial(conn, **key, n_designs=n_ab_designs)
        except Exception as exc:
            # plan_trial can raise TRANSIENTLY (a read racing the campaign's concurrent
            # heuristics.json/ingest writes — observed as an intermittent KeyError). ISOLATE
            # it: a single crashing candidate must NOT abort the whole planning loop and
            # strand every candidate AFTER it. `synth_memory_relax` (last of 33 pending
            # candidates) sat at 0 trials for hours this way — any transient crash earlier in
            # the list blocked it every drain (2026-06-28 audit). Skip + log; the candidate
            # stays 'candidate' and re-plans on the next drain. NEVER demote (non-terminal).
            print(f"[loop] A/B candidate plan_trial errored (skipped, retries next drain): "
                  f"{key['strategy']} symptom={key['symptom_id']} "
                  f"{key['design_class']}/{key['platform']}: {type(exc).__name__}: {exc}")
            continue
        if trial is None:
            # Gate B is unreachable for this candidate: fewer than n_ab_designs
            # resolvable on-disk subjects, so plan_trial returns None on EVERY drain
            # and the candidate would linger forever, silently (2026-06-23 audit,
            # bug #8). Do NOT demote it (demotion is terminal — diff_and_enqueue
            # won't re-enqueue a symptom that already has a recipe_status row), and
            # do NOT fabricate a 2nd subject (honesty: a 2-design trial is genuinely
            # impossible). Leave it 'candidate' so a later drain auto-retries when
            # the corpus regrows, but SURFACE it: log + an idempotent escalation so a
            # genuinely-good but unvalidatable recipe is visible to the operator.
            print(f"[loop] A/B candidate unvalidatable (insufficient subjects): "
                  f"{key['strategy']} symptom={key['symptom_id']} "
                  f"{key['design_class']}/{key['platform']}")
            if conn is not None:
                try:
                    import escalations
                    escalations.open_escalation(
                        conn, design=f"recipe:{key['strategy']}:{key['symptom_id']}",
                        project_path="", run_id=None,
                        reason="unvalidatable_insufficient_subjects",
                        symptom_id=key["symptom_id"],
                        notes=json.dumps(key, sort_keys=True))
                except Exception:               # surfacing must never break the drain
                    pass
            continue
        strat8 = key["strategy"][:8]
        # Resolve the fix-loop check from the strategy + symptom ONCE per trial so a
        # route/place backend-abort arm is driven by the dedicated apply-then-flow
        # runner and a timing arm by fix_signoff --check timing (2026-06-24).
        check = _symptom_check(conn, key.get("symptom_id"), key.get("strategy"))
        for d in trial["designs"]:
            # For a PLACE arm, carry the SUBJECT's PPL-0024 required die perimeter: the arm
            # copy excludes the subject's backend, so arm B cannot re-read the placer message
            # itself. None for FLW-0024/other place aborts -> arm B falls back to the util
            # lever. This is what lets arm B size a perimeter-meeting die where the arm-A
            # control aborts -> a DECISIVE place verdict instead of a both-abort tie (2026-06-27).
            d_pin_target = (_ppl0024_required_perimeter(d["project_path"])
                            if check == "place" else None)
            for arm in ("A", "B"):
                for r in range(k):
                    src = Path(d["project_path"])
                    dst = src.parent / f"{src.name}_ab{arm}_{strat8}_{r}"
                    if src.is_dir() and not dst.exists():
                        # CRITICAL (2026-06-23 audit, bug #1): exclude reports/ too,
                        # not just backend/+*.gds. A signoff arm's SUBJECT is a
                        # previously-FIXED CLEAN project, so its reports/drc.json,
                        # lvs.json, fix_log.jsonl are clean. If copied in, process_one
                        # reads the stale-clean verdict (_signoff_status) and
                        # short-circuits to _mark_clean BEFORE _run_fix ever runs — so
                        # arm A's R2G_FIX_EXCLUDE and arm B's R2G_FIX_RANK_FIRST never
                        # take effect, both arms do byte-identical work, and NO
                        # nangate45 signoff recipe could ever earn a real win. A fresh
                        # arm must start with no inherited verdict and recompute its own
                        # signoff (the route arm reseeds reports/route.json itself).
                        shutil.copytree(src, dst,
                                        ignore=shutil.ignore_patterns(
                                            "backend", "*.gds", "reports"))
                        # Repoint SDC_FILE at the arm's OWN constraint.sdc so its flow (and
                        # period_relax's SDC edit) actually take effect, not the subject's
                        # failing-period SDC (2026-06-25 SDC-pinning fix).
                        _localize_arm_sdc(dst)
                    arm_entry = {"design": dst.name, "project_path": str(dst),
                                 "platform": key["platform"], "kind": "ab_arm",
                                 "arm": arm, "strategy": key["strategy"], "repeat": r,
                                 "check": check,
                                 "ab_key": key, "match_level": trial["match_level"]}
                    if d_pin_target:
                        arm_entry["pin_perimeter_target"] = d_pin_target
                    led.add(arm_entry)
                    appended += 1
    return appended


def learn_cycle(led: Ledger, conn, *, prev_heur: dict | None,
                n_ab_designs: int = 2) -> dict:
    """learn -> diff -> enqueue candidates -> plan A/B trials -> append arm
    entries to the ledger (the SAME loop executes them)."""
    import recipe_lifecycle
    heur = _learn()
    # diff_and_enqueue here is idempotent with learn()'s own enqueue (Gate A):
    # whichever ran first inserts the candidate rows; the second is a no-op.
    recipe_lifecycle.diff_and_enqueue(conn, heur, prev=prev_heur)
    plan_arms_for_candidates(led, conn, n_ab_designs=n_ab_designs)
    return heur


def _ondisk_timing(project_path: str) -> tuple[str | None, float | None]:
    """The arm's ON-DISK timing verdict — (timing_check.json tier, ppa.json setup_wns) —
    a fallback for _arm_metric when the runs row missed the timing signal (a --check
    timing reflow can ingest a ppa.json without finish timing). Best-effort; any read
    failure yields None for that component."""
    proj = Path(project_path)
    tier = wns = None
    try:
        tier = json.loads(
            (proj / "reports" / "timing_check.json").read_text()).get("tier")
    except Exception:
        pass
    try:
        ppa = json.loads((proj / "reports" / "ppa.json").read_text())
        wns = ((ppa.get("summary") or {}).get("timing") or {}).get("setup_wns")
        wns = float(wns) if wns is not None else None
    except Exception:
        pass
    return tier, wns


def _synth_cleared_ondisk(project_path: str) -> bool:
    """True iff the arm's newest backend run got PAST synth (synth stage status 0). Arm A
    (control) of a synth_memory_relax trial memcap-aborts at synth (status 2) -> False; arm
    B (recipe: raise cap + die-pair) clears it -> True. The judge metric for a synth arm,
    analogous to the timing arm's wns: judge on the symptom the recipe FIXES, since a
    synth-recovered FF-memory design may still carry downstream DRC/LVS residuals that would
    tie both arms on the generic is_success (2026-06-28)."""
    logs = sorted(Path(project_path).glob("backend/RUN_*/stage_log.jsonl"))
    if not logs:
        return False
    try:
        rows = [json.loads(ln) for ln in logs[-1].read_text().splitlines() if ln.strip()]
    except Exception:
        return False
    for r in rows:
        if r.get("stage") == "synth":
            return r.get("status") in (0, "0", "pass")
    return False


def _arm_metric(conn, project_path: str, *, timing: bool = False,
                synth: bool = False) -> dict | None:
    """Latest run row for an arm dir -> the metric dict judge_repeated consumes
    (or None if the arm produced no judgeable run). outcome_score is captured as
    an ORDERING HINT only — the verdict never depends on it (invariant H4).

    For a TIMING arm, success is whether the design CLOSED timing (timing_tier in
    {clean,minor} or WNS>=0), NOT the generic is_success: a timing miss does NOT abort
    the flow, so both arms reach a GDS and knowledge_db.is_success reads true for both
    -> every timing trial would be a tie -> inconclusive forever (2026-06-24 audit,
    bug #3-timing). The timing signal is the ingested wns_ns/timing_tier."""
    import knowledge_db
    row = conn.execute(
        "SELECT total_elapsed_s, fix_iters_to_clean, drc_status, lvs_status, "
        "rcx_status, lvs_mismatch_class, orfs_status, outcome_score, "
        "wns_ns, timing_tier "
        "FROM runs WHERE project_path=? ORDER BY ingested_at DESC LIMIT 1",
        (project_path,)).fetchone()
    if row is None:
        return None
    cols = ("total_elapsed_s", "fix_iters_to_clean", "drc_status", "lvs_status",
            "rcx_status", "lvs_mismatch_class", "orfs_status", "outcome_score",
            "wns_ns", "timing_tier")
    r = dict(zip(cols, row))
    if timing:
        tier, wns = r.get("timing_tier"), r.get("wns_ns")
        if tier is None and wns is None:
            # A --check timing reflow can leave the latest runs row's wns_ns/timing_tier
            # null (ingest read a ppa.json without finish timing); fall back to the arm's
            # ON-DISK timing verdict so a genuinely-closed arm isn't judged a failure
            # (2026-06-25). The verdict is the timing_check.json tier / ppa setup_wns.
            tier, wns = _ondisk_timing(project_path)
        success = (tier in ("clean", "minor")) or (wns is not None and wns >= 0)
    elif synth:
        # synth_memory_relax fixes the SYNTH memcap abort: judge on whether the flow got
        # PAST synth, not full signoff (an FF-expanded design may carry downstream DRC/LVS
        # residuals that would tie both arms on is_success — the timing-arm lesson).
        success = _synth_cleared_ondisk(project_path)
    else:
        success = knowledge_db.is_success(r)
    return {"is_success": bool(success),
            "wall_s": r["total_elapsed_s"], "fix_iters": r["fix_iters_to_clean"],
            "outcome_score": r["outcome_score"]}


def judge_finished_trials(led: Ledger, conn) -> None:
    """Group finished A/B arm REPEATS by (base design, strategy) and record a
    variance-aware (LCB) verdict per trial (Win 2)."""
    import ab_runner
    arms = [e for e in led.entries() if e["kind"] == "ab_arm"
            and e["state"] in ("clean", "escalated", "abandoned")
            and not e.get("judged")]
    by_pair: dict[tuple, dict[str, list]] = {}
    for e in arms:
        base = e["design"].rsplit("_ab", 1)[0]
        by_pair.setdefault((base, e["strategy"]), {}).setdefault(e["arm"], []).append(e)
    for (base, strat), pair in by_pair.items():
        if set(pair) != {"A", "B"}:
            continue
        # A timing recipe's arms both reach a GDS (a timing miss never aborts the flow),
        # so judge on the ingested timing verdict (wns_ns/timing_tier), not is_success.
        timing = strat in _TIMING_STRATEGIES
        synth = strat in _SYNTH_STRATEGIES
        samples = {arm: [_arm_metric(conn, e["project_path"], timing=timing, synth=synth)
                         for e in entries]
                   for arm, entries in pair.items()}
        # If an arm produced NO judgeable run at all (incomplete clone/flow — see
        # _process_backend_ab_arm, bug #3), record NO verdict: a trial that never
        # actually ran must not demote a recipe. Mark the arms judged (so the loop
        # does not re-scan + re-query them every drain — unbounded per-turn DB work
        # over a multi-day campaign); the candidate stays 'candidate' and is
        # re-planned with fresh arm entries on the next drain (2026-06-23 review).
        if any(all(s is None for s in samples[arm]) for arm in pair):
            for entries in pair.values():
                for e in entries:
                    led.set_state(e["design"], e["state"], judged=True)
            continue
        verdict = ab_runner.judge_repeated(samples["A"], samples["B"])
        ab_runner.record_trial(
            conn, key=pair["B"][0]["ab_key"], verdict=verdict,
            arm_a_run_id=None, arm_b_run_id=None,
            metrics={"A_samples": samples["A"], "B_samples": samples["B"],
                     "repeats": {"A": len(samples["A"]), "B": len(samples["B"])}},
            match_level=pair["B"][0].get("match_level"))
        for entries in pair.values():
            for e in entries:
                led.set_state(e["design"], e["state"], judged=True)


def ab_workers() -> int:
    """How many A/B arm flows to run CONCURRENTLY (R2G_AB_WORKERS, default 1).
    Each arm is a full ORFS flow; the 96-core host comfortably runs several at
    once, so the drain wall-clock drops from sum-of-arms to slowest-arm-batch."""
    try:
        return max(1, int(os.environ.get("R2G_AB_WORKERS", "1")))
    except ValueError:
        return 1


def _drain_arm(led: "Ledger", entry: dict, db_path: Path | str | None) -> None:
    """Run ONE arm in its OWN db connection — sqlite3 connections are not
    thread-shareable, and the heavy work (flow/fix/ingest) is subprocess-based, so
    each worker thread just needs a private conn for the occasional escalation
    write. The Ledger is lock-guarded (thread-safe)."""
    import knowledge_db
    conn = knowledge_db.connect(db_path) if db_path else knowledge_db.connect()
    try:
        process_one(led, entry, conn)
    finally:
        conn.close()


def ab_drain(ledger_path: Path, *, n_ab_designs: int = 2,
             db_path: Path | str | None = None, max_workers: int | None = None) -> int:
    """Fire A/B trials for already-enqueued candidate recipes WITHOUT re-running
    the normal designs. This is the production "drain the A/B queue" button: the
    batch driver ingests + learns (which now enqueues candidates, Gate A), then a
    periodic ab_drain plans the arms, runs only those arm flows, and judges.

    Arm flows run CONCURRENTLY when R2G_AB_WORKERS > 1 (or max_workers is passed):
    arms are independent ORFS flows, so parallelism turns sum-of-arms wall-clock
    into slowest-batch wall-clock on the multi-core host. Returns trials judged.
    """
    import knowledge_db
    led = Ledger(ledger_path)
    conn = knowledge_db.connect(db_path) if db_path else knowledge_db.connect()
    knowledge_db.ensure_schema(conn)
    plan_arms_for_candidates(led, conn, n_ab_designs=n_ab_designs)
    pending = [e for e in led.pending() if e.get("kind") == "ab_arm"]
    workers = max_workers if max_workers is not None else ab_workers()
    before = conn.execute("SELECT COUNT(*) FROM ab_trials").fetchone()[0]
    # Judge INCREMENTALLY: a pair's verdict is recorded the instant BOTH its arms reach a
    # terminal state, instead of waiting for the whole drain to finish. A drain bundles fast
    # place arms with slow timing/large-rerun arms, and the old end-of-drain judge made a
    # finished promotion wait hours on the slowest unrelated arm (2026-06-27 latency finding:
    # wave 11 surfaced its place wins only after a ~12h drain). judge_finished_trials only
    # acts on pairs whose arms are BOTH terminal (a still-running arm's pair is skipped) and
    # is idempotent (it marks judged), so per-completion calls never partial-judge or
    # double-record; the Ledger is in-memory + lock-guarded so the rescans are cheap.
    if workers > 1 and len(pending) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_drain_arm, led, e, db_path) for e in pending]
            for f in as_completed(futs):
                f.result()
                judge_finished_trials(led, conn)
    else:
        for entry in pending:
            process_one(led, entry, conn)
            judge_finished_trials(led, conn)
    judge_finished_trials(led, conn)                 # final sweep (covers the empty-pending case)
    after = conn.execute("SELECT COUNT(*) FROM ab_trials").fetchone()[0]
    conn.close()
    return after - before


def _safe_process(led: Ledger, entry: dict) -> None:
    """Run one design in a worker thread; a crash in ONE design must never abort
    the whole parallel batch, so escalate-and-continue on any unexpected error."""
    try:
        _drain_arm(led, entry, None)        # private conn per thread + lock-guarded ledger
    except Exception as exc:                # noqa: BLE001 — last-resort batch guard
        try:
            led.set_state(entry["design"], "escalated",
                          reason=f"worker_exc:{type(exc).__name__}")
        except Exception:
            pass


def _run_parallel(led: Ledger, conn, prev_heur: dict | None, *,
                  max_designs: int | None, max_workers: int) -> None:
    """Parallel campaign mode (engineer_loop run --workers N). Run pending NORMAL
    design flows CONCURRENTLY — each is an isolated ORFS subprocess with a private
    DB connection; the Ledger is lock-guarded, so this reuses ab_drain's proven
    thread model. THEN learn once over the batch, enqueue candidate recipes
    (Gate A), plan the A/B arms and drain them in parallel, and judge — so the
    full closed-loop A/B semantics of the serial run are preserved, but the
    wall-clock collapses from sum-of-flows to slowest-batch.

    SAFETY: cap per-flow openroad threads with the NUM_CORES env var so that
    `NUM_CORES * max_workers <= host cores` (no oversubscription). Distinct project
    dirs give distinct FLOW_VARIANTs, so concurrent flows never collide on the
    DESIGN_NAME+FLOW_VARIANT hard rule. Keep workers low when >100K-cell LVS jobs
    may run concurrently (skill hard rule)."""
    import recipe_lifecycle
    pending = [e for e in led.pending() if e.get("kind", "normal") == "normal"]
    if max_designs:
        pending = pending[:max_designs]
    if pending:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(lambda e: _safe_process(led, e), pending))
    # Learn once over the batch results, then enqueue candidate recipes. This is
    # the Gate A step (learn() also enqueues; diff_and_enqueue is idempotent).
    heur = _learn()
    recipe_lifecycle.diff_and_enqueue(conn, heur, prev=prev_heur)
    # Plan A/B arms for the freshly-enqueued candidates, drain them concurrently,
    # then judge -> records the ab_trials verdict + promotes/demotes the recipe.
    plan_arms_for_candidates(led, conn)
    arms = [e for e in led.pending() if e.get("kind") == "ab_arm"]
    if arms:
        # Judge INCREMENTALLY as each arm pair completes (2026-06-27 latency fix): a finished
        # place win surfaces + promotes mid-drain instead of waiting on the slowest unrelated
        # arm (a ~12h wave-11 drain hid its promotions until the end). judge_finished_trials
        # skips pairs not BOTH-terminal and is idempotent, so per-completion calls are safe.
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_safe_process, led, e) for e in arms]
            for f in as_completed(futs):
                f.result()
                judge_finished_trials(led, conn)
    judge_finished_trials(led, conn)


# ---- Fmax pre-pass (search the best closing period for each design) ----------

def _sdc_clk_period(sdc_text: str) -> float | None:
    """The current `set clk_period <N>` value in an SDC, or None (mirrors fmax_model's
    _CLK_RE). Used to make the Fmax stamp IDEMPOTENT on the SDC content itself."""
    m = re.search(r"set\s+clk_period\s+([0-9.]+)", sdc_text)
    return float(m.group(1)) if m else None


def _period_stamped(cur: float | None, period: float) -> bool:
    """True iff the SDC's clk_period equals `period` AS STAMPED. rewrite_clk_period writes
    `{period:g}` (6 sig-figs), so the old full-precision compare with a 1e-9 tolerance
    falsely rejected ~28% of CORRECT stamps (2026-06-26 audit, fmax bug #1): a winner of
    0.69180034 is written as '0.6918', read back as 0.6918, and |0.6918-0.69180034|=3.5e-7
    > 1e-9 -> the stamp returned None and was counted as a no-op (under-reporting the
    characterized count + defeating the anti-no-op honesty defense). Compare against the
    %g-formatted value so a real stamp counts; a genuine no-op (clockless / wrong value)
    still returns False."""
    return cur is not None and cur == float(f"{period:g}")


def _fmax_one(entry: dict, *, place_fast: bool = True) -> float | str | None:
    """Characterize ONE design's best closing period (the proxy Fmax search) and STAMP
    its constraints/constraint.sdc with the winner so the campaign flow signs off at the
    fastest clock that still closes. Shells out to the tested fmax_search.py CLI (which
    writes reports/fmax_search.json — the user-facing Fmax deliverable). Returns the
    winner period (float) ONLY when the SDC was actually stamped to it, the search status
    string on a non-ok/clockless result, or None when nothing usable was produced.

    Idempotency keys on the SDC STAMP, not report existence (2026-06-24 review L4-02):
    fmax_search.py always writes the report even when the canonical SDC was never stamped
    (it only rewrites ephemeral variant SDCs), so re-running compares the SDC's current
    clk_period to the winner and re-stamps on drift — never short-circuits on the report
    alone. The stamp is then VERIFIED (re-read) so a silent no-op returns None and is not
    counted (defends against the L4-01 import-mask). `import fmax_model` resolves because
    scripts/reports/ is on sys.path at module load (not swallowed in a bare except)."""
    import fmax_model                            # scripts/reports on sys.path (module load)
    proj = Path(entry["project_path"])
    rep = proj / "reports" / "fmax_search.json"
    sdc = proj / "constraints" / "constraint.sdc"

    def _report_period():
        try:
            data = json.loads(rep.read_text())
        except Exception:
            return None, None
        if data.get("status") != "ok" or "winner" not in data:
            return None, data.get("status")
        return (data.get("winner") or {}).get("period"), "ok"

    period, status = _report_period()
    # Already stamped to this winner? -> truly idempotent (no re-run, no rewrite).
    if isinstance(period, (int, float)) and sdc.exists():
        cur = _sdc_clk_period(sdc.read_text())
        if _period_stamped(cur, period):
            return period
    # No usable winner on disk yet -> run the proxy search (bounded cost; no --verify).
    if not isinstance(period, (int, float)):
        fmax = _script("R2G_LOOP_FMAX", REPORTS / "fmax_search.py")
        cmd = [sys.executable, fmax, str(proj), entry.get("platform", "nangate45")]
        if place_fast:
            cmd.append("--place-fast")
        subprocess.run(cmd)
        period, status = _report_period()
        if not isinstance(period, (int, float)):
            return status                       # no_clock_constraint / inconclusive / None
    # Stamp the canonical SDC. A one-time PRE-flow config change (not a re-derivation of
    # history): the subsequent flow regenerates ppa.json -> a fresh run_id, so multi-run
    # history holds. config.mk already pins SDC_FILE at this constraint.sdc.
    if not sdc.exists():
        return status
    try:
        new = fmax_model.rewrite_clk_period(sdc.read_text(), period)
    except ValueError:                          # clockless SDC: leave as-is, honest
        return status
    sdc.write_text(new, encoding="utf-8")
    # VERIFY the stamp landed — a no-op must be uncountable (review L4-01/L4-02). Compare
    # against the %g-formatted value (rewrite_clk_period's format), not full precision, so
    # a correct stamp is not falsely rejected by float rounding (2026-06-26 audit).
    cur = _sdc_clk_period(sdc.read_text())
    return period if _period_stamped(cur, period) else None


def fmax_drain(ledger_path: Path, *, platform: str | None = None,
               max_workers: int = 1, place_fast: bool = True,
               max_designs: int | None = None) -> int:
    """Run the proxy Fmax search for the pending NORMAL designs in the ledger and stamp
    each one's SDC with its best closing period (so the subsequent `run` flows + signs
    off at Fmax). Cross-design parallelism via max_workers (the per-design search is
    sequential ~7-8 place probes; cap max_workers*NUM_CORES <= host cores per the
    parallel-ORFS hard rule). `max_designs` bounds the batch to the first N pending — the
    SAME prefix `run --max N` picks — so a driver can interleave Fmax + signoff per wave
    (fast feedback) rather than Fmax-ing all pending up front. Returns the count of
    designs that got a real period."""
    led = Ledger(ledger_path)
    pending = [e for e in led.pending() if e.get("kind", "normal") == "normal"]
    if platform:
        pending = [e for e in pending if e.get("platform") == platform]
    if max_designs:
        pending = pending[:max_designs]

    def _one(e: dict):
        try:
            return _fmax_one(e, place_fast=place_fast)
        except Exception:                       # one degenerate design must not abort all
            return None

    if max_workers > 1 and len(pending) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(_one, pending))
    else:
        results = [_one(e) for e in pending]
    return sum(1 for r in results if isinstance(r, (int, float)))


def run(ledger_path: Path, *, max_designs: int | None = None,
        max_workers: int = 1) -> None:
    import knowledge_db
    led = Ledger(ledger_path)
    conn = knowledge_db.connect()
    knowledge_db.ensure_schema(conn)
    prev_heur = None
    hp = KNOWLEDGE / "heuristics.json"
    if hp.exists():
        prev_heur = json.loads(hp.read_text())
    if max_workers and max_workers > 1:
        _run_parallel(led, conn, prev_heur, max_designs=max_designs,
                      max_workers=max_workers)
        conn.close()
        return
    done = 0
    while True:
        pending = led.pending()
        if not pending or (max_designs and done >= max_designs):
            break
        entry = pending[0]
        process_one(led, entry, conn)
        done += 1
        heur = learn_cycle(led, conn, prev_heur=prev_heur)
        judge_finished_trials(led, conn)
        prev_heur = heur
    conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run")
    pr.add_argument("--ledger", required=True, type=Path)
    pr.add_argument("--max", type=int, default=None)
    pr.add_argument("--workers", type=int, default=1,
                    help="run this many design flows concurrently (cap NUM_CORES so "
                         "workers*NUM_CORES <= host cores; see SKILL hard rules)")
    pa = sub.add_parser("add")
    pa.add_argument("--ledger", required=True, type=Path)
    pa.add_argument("--project", required=True)
    pa.add_argument("--platform", default="nangate45")
    ps = sub.add_parser("status")
    ps.add_argument("--ledger", required=True, type=Path)
    pd = sub.add_parser("ab-drain", help="fire A/B trials for pending candidates")
    pd.add_argument("--ledger", required=True, type=Path)
    pd.add_argument("--n-designs", type=int, default=2)
    pd.add_argument("--workers", type=int, default=None,
                    help="run this many arm flows concurrently (default R2G_AB_WORKERS or 1)")
    pf = sub.add_parser("fmax-drain",
                        help="proxy-search the best closing period for each pending "
                             "design + stamp its SDC (run BEFORE `run`)")
    pf.add_argument("--ledger", required=True, type=Path)
    pf.add_argument("--platform", default=None,
                    help="only Fmax-search designs on this platform")
    pf.add_argument("--max", type=int, default=None,
                    help="bound to the first N pending (the same prefix `run --max N` "
                         "picks) so Fmax + signoff can interleave per wave")
    pf.add_argument("--workers", type=int, default=1,
                    help="characterize this many designs concurrently (each search is "
                         "sequential; cap workers*NUM_CORES <= host cores)")
    pf.add_argument("--no-place-fast", action="store_true",
                    help="disable PLACE_FAST in the place probes (slower, more accurate)")
    pe = sub.add_parser("ab-enqueue",
                        help="force a (grandfathered) recipe into A/B candidate")
    pe.add_argument("--symptom", required=True)
    pe.add_argument("--design-class", required=True)
    pe.add_argument("--platform", required=True)
    pe.add_argument("--strategy", required=True)
    pm = sub.add_parser("demote",
                        help="flip a recipe to 'shadow' (operator-gated; the verb "
                             "detect_contradictions.py emits). Never auto-applied.")
    pm.add_argument("--symptom", required=True)
    pm.add_argument("--design-class", required=True)
    pm.add_argument("--platform", required=True)
    pm.add_argument("--strategy", required=True)
    pm.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        run(args.ledger, max_designs=args.max, max_workers=args.workers)
    elif args.cmd == "add":
        led = Ledger(args.ledger)
        led.add({"design": Path(args.project).name,
                 "project_path": str(Path(args.project).resolve()),
                 "platform": args.platform})
    elif args.cmd == "ab-drain":
        n = ab_drain(args.ledger, n_ab_designs=args.n_designs,
                     max_workers=args.workers)
        print(f"ab_drain judged {n} trial(s)")
    elif args.cmd == "fmax-drain":
        n = fmax_drain(args.ledger, platform=args.platform,
                       max_workers=args.workers, max_designs=args.max,
                       place_fast=not args.no_place_fast)
        print(f"fmax_drain characterized {n} design(s)")
    elif args.cmd == "ab-enqueue":
        import knowledge_db
        import recipe_lifecycle
        conn = knowledge_db.connect()
        knowledge_db.ensure_schema(conn)
        created = recipe_lifecycle.enqueue_candidate(
            conn, symptom_id=args.symptom, design_class=args.design_class,
            platform=args.platform, strategy=args.strategy)
        conn.close()
        print("enqueued" if created else "already in lifecycle (no-op)")
    elif args.cmd == "demote":
        import knowledge_db
        import recipe_lifecycle
        # Open the canonical store explicitly (module global, so tests that patch
        # knowledge_db.DEFAULT_DB_PATH redirect this). recipe_lifecycle.demote is an
        # idempotent UPSERT to status='shadow', stamping reason into provenance.
        conn = knowledge_db.connect(knowledge_db.DEFAULT_DB_PATH)
        knowledge_db.ensure_schema(conn)
        recipe_lifecycle.demote(
            conn, reason=args.reason, symptom_id=args.symptom,
            design_class=args.design_class, platform=args.platform,
            strategy=args.strategy)
        conn.commit()
        conn.close()
        print(f"demoted {args.strategy} for symptom {args.symptom} "
              f"({args.design_class}/{args.platform}) -> shadow")
    else:
        led = Ledger(args.ledger)
        from collections import Counter
        for state, n in Counter(e["state"] for e in led.entries()).items():
            print(f"{state:10s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
