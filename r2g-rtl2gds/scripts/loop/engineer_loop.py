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
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = SKILL_ROOT / "knowledge"
FLOW = SKILL_ROOT / "scripts" / "flow"
sys.path.insert(0, str(KNOWLEDGE))

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
                cur = self._entries.setdefault(e["design"], e)
                cur.update(e)

    def _append(self, obj: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, sort_keys=True) + "\n")

    def add(self, entry: dict) -> None:
        e = dict(entry)
        e.setdefault("kind", "normal")
        e.setdefault("state", "pending")
        e["ts"] = _now()
        with self._lock:
            self._entries[e["design"]] = dict(self._entries.get(e["design"], {}), **e)
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


def _symptom_check(conn, symptom_id: str | None) -> str:
    """Map a symptom_id to the fix_signoff.sh --check value. A backend-stage abort
    (check=orfs_stage) is fixed BEFORE signoff via --check <stage> (only 'route'
    has a v1 fixer); everything else is a post-route signoff fix (--check both)."""
    if not symptom_id:
        return "both"
    row = conn.execute(
        "SELECT check_type, class FROM symptoms WHERE symptom_id=?",
        (symptom_id,)).fetchone() if conn is not None else None
    if row and row[0] == "orfs_stage" and row[1] == "route":
        return "route"
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
    """Apply the recipe's backend strategy (e.g. route_relief) into the arm's
    config.mk BEFORE its single flow run. Seeds a fail route.json so diagnose can
    resolve the route strategy (no backend exists yet to extract from)."""
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
    """A/B arm for a BACKEND-ABORT symptom (orfs_stage/route). Unlike a signoff
    arm (flow succeeds -> signoff fails -> fix), a route arm's 'fix' IS a config
    retune + re-route. So we apply the strategy up-front on arm B and run the flow
    EXACTLY ONCE per arm: arm A is the control (default util -> route times out ->
    is_success False); arm B is route_relief (lower util -> route completes ->
    is_success True). judge -> win. One flow per arm (no wasted control-config
    route on arm B)."""
    design = entry["design"]
    if entry.get("arm") == "B":
        led.set_state(design, "fixing")
        _apply_recipe_strategy(entry)
    led.set_state(design, "flow")
    rc = _run_flow(entry)
    _ingest(entry)
    # The judge reads the ingested run's is_success; rc only drives the ledger
    # terminal state (clean vs escalated) so judge_finished_trials picks it up.
    led.set_state(design, "clean" if rc == 0 else "escalated",
                  **({} if rc == 0 else {"reason": "route_arm_failed"}))


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


def _ingest(entry: dict) -> str | None:
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


def process_one(led: Ledger, entry: dict, conn) -> None:
    design = entry["design"]
    if entry.get("kind") == "ab_arm":
        _journal_ab_launch(entry)           # Tier B1 — advisory decision telemetry
    # Backend-abort A/B arm (route congestion): the flow itself fails at the
    # backend stage, so the signoff "flow -> fix" model does not apply — route it
    # through the dedicated apply-then-flow arm runner. (2026-06-17 route-relief.)
    if entry.get("kind") == "ab_arm" and entry.get("check") == "route":
        _process_backend_ab_arm(led, entry, conn)
        return
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
        reason, notes = "unseen_crash", f"run_orfs rc={rc}"
        if entry.get("kind") != "ab_arm" and _fail_stage(entry) == "route":
            led.set_state(design, "fixing")
            fix_rc = _run_fix({**entry, "check": "route"})
            _ingest(entry)
            if fix_rc == 0:
                _mark_clean(led, conn, design, "route_relief cleared the abort in-loop")
                return
            # route_relief ran but did NOT clear the abort — a KNOWN, recipe-backed
            # backend residual (congestion past the CORE_UTILIZATION floor, or a
            # DIE_AREA-sized design with no util knob to relieve), NOT an "unseen
            # crash". Mislabeling it unseen_crash pollutes the escalation queue and
            # the learning signal (it reads as a novel symptom). Label it honestly so
            # the operator runbook can route it to the v2 DIE_AREA lever.
            reason = "route_congestion_residual"
            notes = (f"route abort (rc={rc}); route_relief exhausted or inapplicable "
                     f"(util at floor, or DIE_AREA-sized — no CORE_UTILIZATION knob)")
        led.set_state(design, "escalated", reason=reason)
        if conn is not None:
            import escalations
            escalations.open_escalation(
                conn, design=design, project_path=entry["project_path"],
                run_id=None, reason=reason, notes=notes)
        return
    led.set_state(design, "signoff")
    status = _signoff_status(entry)
    if all(v in ("clean", "clean_beol", "skipped") for v in status.values()):
        _ingest(entry)
        _mark_clean(led, conn, design, "signoff clean on first pass")
        return
    led.set_state(design, "fixing")
    fix_rc = _run_fix(entry)
    _ingest(entry)
    if fix_rc == 0:
        _mark_clean(led, conn, design, "signoff fix cleared residual")
    else:
        led.set_state(design, "escalated", reason="catalog_exhausted")
        if conn is not None:
            import escalations
            escalations.open_escalation(
                conn, design=design, project_path=entry["project_path"],
                run_id=None, reason="catalog_exhausted",
                notes=json.dumps(status, sort_keys=True))


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
        trial = ab_runner.plan_trial(conn, **key, n_designs=n_ab_designs)
        if trial is None:
            continue
        strat8 = key["strategy"][:8]
        # Resolve the fix-loop check from the symptom ONCE per trial so a route
        # (backend-abort) arm is driven by the dedicated apply-then-flow runner.
        check = _symptom_check(conn, key.get("symptom_id"))
        for d in trial["designs"]:
            for arm in ("A", "B"):
                for r in range(k):
                    src = Path(d["project_path"])
                    dst = src.parent / f"{src.name}_ab{arm}_{strat8}_{r}"
                    if src.is_dir() and not dst.exists():
                        shutil.copytree(src, dst,
                                        ignore=shutil.ignore_patterns("backend", "*.gds"))
                    led.add({"design": dst.name, "project_path": str(dst),
                             "platform": key["platform"], "kind": "ab_arm",
                             "arm": arm, "strategy": key["strategy"], "repeat": r,
                             "check": check,
                             "ab_key": key, "match_level": trial["match_level"]})
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


def _arm_metric(conn, project_path: str) -> dict | None:
    """Latest run row for an arm dir -> the metric dict judge_repeated consumes
    (or None if the arm produced no judgeable run). outcome_score is captured as
    an ORDERING HINT only — the verdict never depends on it (invariant H4)."""
    import knowledge_db
    row = conn.execute(
        "SELECT total_elapsed_s, fix_iters_to_clean, drc_status, lvs_status, "
        "rcx_status, lvs_mismatch_class, orfs_status, outcome_score "
        "FROM runs WHERE project_path=? ORDER BY ingested_at DESC LIMIT 1",
        (project_path,)).fetchone()
    if row is None:
        return None
    cols = ("total_elapsed_s", "fix_iters_to_clean", "drc_status", "lvs_status",
            "rcx_status", "lvs_mismatch_class", "orfs_status", "outcome_score")
    r = dict(zip(cols, row))
    return {"is_success": knowledge_db.is_success(r),
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
        samples = {arm: [_arm_metric(conn, e["project_path"]) for e in entries]
                   for arm, entries in pair.items()}
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
    if workers > 1 and len(pending) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda e: _drain_arm(led, e, db_path), pending))
    else:
        for entry in pending:
            process_one(led, entry, conn)
    before = conn.execute("SELECT COUNT(*) FROM ab_trials").fetchone()[0]
    judge_finished_trials(led, conn)
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
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(lambda e: _safe_process(led, e), arms))
    judge_finished_trials(led, conn)


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
