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
        self._entries[e["design"]] = dict(self._entries.get(e["design"], {}), **e)
        self._append(e)

    def set_state(self, design: str, state: str, **extra) -> None:
        if state not in STATES:
            raise ValueError(f"illegal state: {state}")
        e = {"design": design, "state": state, "ts": _now(), **extra}
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


def _run_fix(entry: dict) -> int:
    env = dict(os.environ)
    if entry.get("kind") == "ab_arm":
        if entry.get("arm") == "A":
            env["R2G_FIX_EXCLUDE"] = entry["strategy"]
        else:
            env["R2G_FIX_RANK_FIRST"] = entry["strategy"]
    return subprocess.run(
        ["bash", _script("R2G_LOOP_FIX", FLOW / "fix_signoff.sh"),
         entry["project_path"], entry["platform"], "--check", "both"],
        env=env).returncode


def _ingest(entry: dict) -> str | None:
    r = subprocess.run(
        [sys.executable, _script("R2G_LOOP_INGEST", KNOWLEDGE / "ingest_run.py"),
         entry["project_path"]], capture_output=True, text=True)
    for tok in (r.stdout or "").split():
        if tok.startswith("run_id="):
            return tok.split("=", 1)[1]
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

def process_one(led: Ledger, entry: dict, conn) -> None:
    design = entry["design"]
    led.set_state(design, "flow")
    rc = _run_flow(entry)
    if rc != 0:
        led.set_state(design, "escalated", reason="unseen_crash")
        if conn is not None:
            import escalations
            escalations.open_escalation(
                conn, design=design, project_path=entry["project_path"],
                run_id=None, reason="unseen_crash",
                notes=f"run_orfs rc={rc}")
        _ingest(entry)                      # partial runs still teach
        return
    led.set_state(design, "signoff")
    status = _signoff_status(entry)
    if all(v in ("clean", "clean_beol", "skipped") for v in status.values()):
        _ingest(entry)
        led.set_state(design, "clean")
        return
    led.set_state(design, "fixing")
    fix_rc = _run_fix(entry)
    _ingest(entry)
    if fix_rc == 0:
        led.set_state(design, "clean")
    else:
        led.set_state(design, "escalated", reason="catalog_exhausted")
        if conn is not None:
            import escalations
            escalations.open_escalation(
                conn, design=design, project_path=entry["project_path"],
                run_id=None, reason="catalog_exhausted",
                notes=json.dumps(status, sort_keys=True))


def plan_arms_for_candidates(led: Ledger, conn, *, n_ab_designs: int = 2) -> int:
    """For every pending candidate recipe, plan an A/B trial and append its arm
    entries to the ledger (the SAME loop — or ab_drain — executes them). Returns
    the number of arm entries appended. Idempotent on the arm dirs (skips a dst
    that already exists)."""
    import ab_runner
    import recipe_lifecycle
    appended = 0
    for key in recipe_lifecycle.pending_candidates(conn):
        trial = ab_runner.plan_trial(conn, **key, n_designs=n_ab_designs)
        if trial is None:
            continue
        strat8 = key["strategy"][:8]
        for d in trial["designs"]:
            for arm in ("A", "B"):
                src = Path(d["project_path"])
                dst = src.parent / f"{src.name}_ab{arm}_{strat8}"
                if src.is_dir() and not dst.exists():
                    shutil.copytree(src, dst,
                                    ignore=shutil.ignore_patterns("backend", "*.gds"))
                led.add({"design": dst.name, "project_path": str(dst),
                         "platform": key["platform"], "kind": "ab_arm",
                         "arm": arm, "strategy": key["strategy"],
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


def judge_finished_trials(led: Ledger, conn) -> None:
    """Pair finished A/B arms by (base design, strategy) and record verdicts."""
    import ab_runner
    import knowledge_db
    arms = [e for e in led.entries() if e["kind"] == "ab_arm"
            and e["state"] in ("clean", "escalated", "abandoned")
            and not e.get("judged")]
    by_pair: dict[tuple, dict] = {}
    for e in arms:
        base = e["design"].rsplit("_ab", 1)[0]
        by_pair.setdefault((base, e["strategy"]), {})[e["arm"]] = e
    for (base, strat), pair in by_pair.items():
        if set(pair) != {"A", "B"}:
            continue
        metrics = {}
        for arm, e in pair.items():
            row = conn.execute(
                "SELECT total_elapsed_s, fix_iters_to_clean, drc_status, "
                "lvs_status, rcx_status, lvs_mismatch_class, orfs_status, "
                "outcome_score "
                "FROM runs WHERE project_path=? ORDER BY ingested_at DESC "
                "LIMIT 1", (e["project_path"],)).fetchone()
            if row is None:
                metrics[arm] = None
                continue
            cols = ("total_elapsed_s", "fix_iters_to_clean", "drc_status",
                    "lvs_status", "rcx_status", "lvs_mismatch_class",
                    "orfs_status", "outcome_score")
            r = dict(zip(cols, row))
            # outcome_score is captured as an ORDERING HINT for suggestion ranking
            # (persisted into ab_trials.metrics_json); judge ignores it for the
            # verdict — a non-clean arm never wins (Win 1 invariant H4).
            metrics[arm] = {"is_success": knowledge_db.is_success(r),
                            "wall_s": r["total_elapsed_s"],
                            "fix_iters": r["fix_iters_to_clean"],
                            "outcome_score": r["outcome_score"]}
        verdict = ab_runner.judge(metrics.get("A"), metrics.get("B"))
        ab_runner.record_trial(
            conn, key=pair["B"]["ab_key"], verdict=verdict,
            arm_a_run_id=None, arm_b_run_id=None,
            metrics=metrics, match_level=pair["B"].get("match_level"))
        for e in pair.values():
            led.set_state(e["design"], e["state"], judged=True)


def ab_drain(ledger_path: Path, *, n_ab_designs: int = 2,
             db_path: Path | str | None = None) -> int:
    """Fire A/B trials for already-enqueued candidate recipes WITHOUT re-running
    the normal designs. This is the production "drain the A/B queue" button: the
    batch driver ingests + learns (which now enqueues candidates, Gate A), then a
    periodic ab_drain plans the arms, runs only those arm flows, and judges.

    Returns the number of trials judged this pass.
    """
    import knowledge_db
    led = Ledger(ledger_path)
    conn = knowledge_db.connect(db_path) if db_path else knowledge_db.connect()
    knowledge_db.ensure_schema(conn)
    plan_arms_for_candidates(led, conn, n_ab_designs=n_ab_designs)
    for entry in [e for e in led.pending() if e.get("kind") == "ab_arm"]:
        process_one(led, entry, conn)
    before = conn.execute("SELECT COUNT(*) FROM ab_trials").fetchone()[0]
    judge_finished_trials(led, conn)
    after = conn.execute("SELECT COUNT(*) FROM ab_trials").fetchone()[0]
    conn.close()
    return after - before


def run(ledger_path: Path, *, max_designs: int | None = None) -> None:
    import knowledge_db
    led = Ledger(ledger_path)
    conn = knowledge_db.connect()
    knowledge_db.ensure_schema(conn)
    prev_heur = None
    hp = KNOWLEDGE / "heuristics.json"
    if hp.exists():
        prev_heur = json.loads(hp.read_text())
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
    pa = sub.add_parser("add")
    pa.add_argument("--ledger", required=True, type=Path)
    pa.add_argument("--project", required=True)
    pa.add_argument("--platform", default="nangate45")
    ps = sub.add_parser("status")
    ps.add_argument("--ledger", required=True, type=Path)
    pd = sub.add_parser("ab-drain", help="fire A/B trials for pending candidates")
    pd.add_argument("--ledger", required=True, type=Path)
    pd.add_argument("--n-designs", type=int, default=2)
    pe = sub.add_parser("ab-enqueue",
                        help="force a (grandfathered) recipe into A/B candidate")
    pe.add_argument("--symptom", required=True)
    pe.add_argument("--design-class", required=True)
    pe.add_argument("--platform", required=True)
    pe.add_argument("--strategy", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        run(args.ledger, max_designs=args.max)
    elif args.cmd == "add":
        led = Ledger(args.ledger)
        led.add({"design": Path(args.project).name,
                 "project_path": str(Path(args.project).resolve()),
                 "platform": args.platform})
    elif args.cmd == "ab-drain":
        n = ab_drain(args.ledger, n_ab_designs=args.n_designs)
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
    else:
        led = Ledger(args.ledger)
        from collections import Counter
        for state, n in Counter(e["state"] for e in led.entries()).items():
            print(f"{state:10s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
