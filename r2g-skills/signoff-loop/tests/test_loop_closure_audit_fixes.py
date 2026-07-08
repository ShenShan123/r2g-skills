"""Regression tests for the 2026-06-23 loop-closure audit fixes.

The audit found the engineer-learning-loop was DEGRADED: it recorded fixes and ran
A/B trials but could NOT promote an entire class of genuinely-good recipes (every
nangate45 signoff recipe). Each test below pins one fix so the regression cannot
silently return. See references/failure-patterns.md "Learning-loop closure" +
docs/superpowers/plans/r2g-loop-closure-audit-2026-06-23.md.
"""
import json

import pytest

import os

import ab_runner
import engineer_loop as el
import ingest_run
import knowledge_db
import recipe_lifecycle


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _s(success, wall, iters=1):
    return {"is_success": success, "wall_s": wall, "fix_iters": iters,
            "outcome_score": 0.88}


# ── bug #2: variance-aware wall-clock tiebreak (no noise flips) ───────────────
def test_judge_subnoise_wall_tie_is_inconclusive():
    # Both arms reliably sign off (success tie); the wall-clock delta is WITHIN the
    # combined sampling noise -> must be 'inconclusive', never a noise win/loss.
    # (The live antenna trials 15/16 flipped win<->loss on <12s of identical work.)
    a = [_s(True, 116), _s(True, 123)]
    b = [_s(True, 113), _s(True, 134)]
    assert ab_runner.judge_repeated(a, b) == "inconclusive"


def test_judge_single_sample_cost_tie_does_not_flip():
    # k=1: no variance estimate at all -> a cost-only difference must NOT decide the
    # verdict (the old flat ±2% band turned pure jitter into win/loss).
    assert ab_runner.judge_repeated([_s(True, 116)], [_s(True, 113)]) == "inconclusive"
    assert ab_runner.judge_repeated([_s(True, 100)], [_s(True, 140)]) == "inconclusive"


def test_judge_robust_cost_difference_still_decides():
    # A genuinely, robustly cheaper arm B (tight, well-separated) still wins.
    a = [_s(True, 200), _s(True, 202)]
    b = [_s(True, 100), _s(True, 101)]
    assert ab_runner.judge_repeated(a, b) == "win"


def test_judge_zero_variance_real_cost_delta_decides():
    # se==0 is MAXIMAL confidence (deterministic), NOT "no confidence": a robustly
    # cheaper jitter-free arm B must WIN, not land inconclusive -> terminal shadow
    # (2026-06-23 review BLOCKER #2 — the se>0 guard inverted this).
    assert ab_runner.judge_repeated([_s(True, 100), _s(True, 100)],
                                    [_s(True, 50), _s(True, 50)]) == "win"
    assert ab_runner.judge_repeated([_s(True, 50), _s(True, 50)],
                                    [_s(True, 100), _s(True, 100)]) == "loss"
    # a deterministic but TRIVIAL delta (<1% of mean) stays inconclusive.
    assert ab_runner.judge_repeated([_s(True, 100), _s(True, 100)],
                                    [_s(True, 99.9), _s(True, 99.9)]) == "inconclusive"


def test_judge_success_rate_win_unaffected():
    # The core promotion path is untouched: B signs off where A does not -> win.
    # (This is how the antenna recipe earns a real win once bug #1 is fixed.)
    assert ab_runner.judge_repeated([_s(False, 120)], [_s(True, 130)]) == "win"


# ── bug #1: a signoff A/B arm must always run the fixer (never short-circuit) ──
def test_signoff_ab_arm_always_runs_fix_despite_clean_report(tmp_path, monkeypatch):
    """An ab_arm that inherited a CLEAN reports/drc.json must STILL reach _run_fix so
    arm A's EXCLUDE / arm B's RANK_FIRST diverge — never short-circuit to clean."""
    proj = tmp_path / "d_abB_antenna_0"
    (proj / "reports").mkdir(parents=True)
    (proj / "reports" / "drc.json").write_text('{"status": "clean"}')
    (proj / "reports" / "lvs.json").write_text('{"status": "clean"}')
    fix_calls = []
    monkeypatch.setattr(el, "_run_flow", lambda e: 0)
    monkeypatch.setattr(el, "_ingest", lambda e: "rid")
    monkeypatch.setattr(el, "_run_fix", lambda e: fix_calls.append(e.get("arm")) or 0)
    monkeypatch.setattr(el, "_mark_clean", lambda *a, **k: None)
    led = el.Ledger(tmp_path / "ledger.jsonl")
    entry = {"design": "d_abB_antenna_0", "project_path": str(proj),
             "platform": "nangate45", "kind": "ab_arm", "arm": "B",
             "strategy": "antenna_diode_repair", "check": "both"}
    led.add(entry)
    el.process_one(led, led.pending()[0], conn=None)
    assert fix_calls == ["B"], "signoff ab_arm short-circuited to clean — fixer never ran"


def test_plan_arms_copytree_excludes_reports(tmp_path, monkeypatch):
    """Arm dirs must NOT inherit the subject's reports/ (stale clean verdict)."""
    src = tmp_path / "subj"
    (src / "reports").mkdir(parents=True)
    (src / "reports" / "drc.json").write_text('{"status": "clean"}')
    (src / "constraints").mkdir()
    conn = _conn(tmp_path)
    key = dict(symptom_id="e5582b51cd0017b9", design_class="logic/unknown",
               platform="nangate45", strategy="antenna_diode_repair")
    monkeypatch.setattr(ab_runner, "plan_trial", lambda *a, **k: {
        "designs": [{"project_path": str(src), "design_name": "subj", "cell_count": 9}],
        "match_level": "fixhist_platform"})
    recipe_lifecycle.enqueue_candidate(conn, **key)
    led = el.Ledger(tmp_path / "ledger.jsonl")
    el.plan_arms_for_candidates(led, conn)
    arms = list(tmp_path.glob("subj_ab*"))
    assert arms, "no arm dirs created"
    for arm in arms:
        assert not (arm / "reports").exists(), f"{arm.name} inherited stale reports/"


# ── bug #3: never ingest a junk 'unknown' row for a flow that never ran ───────
def test_ingest_skips_project_with_no_backend_and_no_ppa(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(el.subprocess, "run",
                        lambda *a, **k: ran.append(a) or pytest.fail("ingest ran"))
    assert el._ingest({"project_path": str(tmp_path)}) is None
    assert ran == []


def test_route_arm_with_no_backend_escalates_not_ingests(tmp_path, monkeypatch):
    proj = tmp_path / "d_abA_route_0"
    proj.mkdir()
    monkeypatch.setattr(el, "_run_flow", lambda e: 2)          # flow aborts, no backend
    monkeypatch.setattr(el, "_ingest", lambda e: pytest.fail("ingested a junk arm"))
    led = el.Ledger(tmp_path / "ledger.jsonl")
    entry = {"design": "d_abA_route_0", "project_path": str(proj),
             "platform": "nangate45", "kind": "ab_arm", "arm": "A",
             "strategy": "route_relief", "check": "route"}
    led.add(entry)
    el.process_one(led, led.pending()[0], conn=None)
    assert led.state("d_abA_route_0") == "escalated"
    assert led._entries["d_abA_route_0"].get("reason") == "route_arm_incomplete"


# ── bug #8: an unvalidatable candidate is surfaced, never silently skipped ────
def test_unvalidatable_candidate_opens_escalation(tmp_path, monkeypatch):
    import escalations
    conn = _conn(tmp_path)
    key = dict(symptom_id="913f3c15479aa474", design_class="logic/unknown",
               platform="nangate45", strategy="period_relax")
    recipe_lifecycle.enqueue_candidate(conn, **key)
    monkeypatch.setattr(ab_runner, "plan_trial", lambda *a, **k: None)  # < n subjects
    led = el.Ledger(tmp_path / "ledger.jsonl")
    el.plan_arms_for_candidates(led, conn)
    rows = conn.execute(
        "SELECT reason, symptom_id FROM escalations WHERE status='open'").fetchall()
    assert any(r[0] == "unvalidatable_insufficient_subjects"
               and r[1] == key["symptom_id"] for r in rows)
    # candidate is NOT demoted (demotion is terminal) — stays drainable.
    assert recipe_lifecycle.get_status(conn, **key) == "candidate"


# ── bug #9(a): a re-ingest with cell_count=NULL must not flip the size band ───
def _mk_run(proj, run_name, *, stages, ppa):
    run = proj / "backend" / run_name
    run.mkdir(parents=True)
    run.joinpath("stage_log.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in stages))
    (proj / "reports" / "ppa.json").write_text(json.dumps(ppa))


def test_reingest_with_null_cellcount_keeps_stable_design_class(tmp_path):
    """A placed run records cell_count=18055 (medium). A later FLW-0024 place-abort
    re-ingest of the SAME project has cell_count=NULL — but its design_class size
    band must stay 'medium' (inherited), not flip to 'unknown' and respawn an
    unvalidated A/B candidate (2026-06-23 audit, bug #9, proximate trigger)."""
    conn = _conn(tmp_path)
    proj = tmp_path / "demo_proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    (proj / "reports").mkdir()

    _mk_run(proj, "RUN_2026-06-01_00-00-00",
            stages=[{"stage": "synth", "status": 0, "elapsed_s": 1},
                    {"stage": "route", "status": 0, "elapsed_s": 1}],
            ppa={"geometry": {"instance_count": 18055}, "summary": {}})
    rid1 = ingest_run.ingest(proj, conn)
    dc1, cc1 = conn.execute(
        "SELECT design_class, cell_count FROM runs WHERE run_id=?", (rid1,)).fetchone()
    assert dc1.endswith("/medium") and cc1 == 18055

    # FLW-0024 place abort: no instance_count -> cell_count NULL. Bump ppa mtime so
    # _compute_run_id yields a DISTINCT run (immutable-history: a new row, not UPSERT).
    _mk_run(proj, "RUN_2026-06-23_00-00-00",
            stages=[{"stage": "synth", "status": 0, "elapsed_s": 1},
                    {"stage": "place", "status": 2, "elapsed_s": 1}],
            ppa={"geometry": {}, "summary": {}})
    ppa = proj / "reports" / "ppa.json"
    os.utime(ppa, (ppa.stat().st_atime + 5, ppa.stat().st_mtime + 5))
    rid2 = ingest_run.ingest(proj, conn)
    assert rid2 != rid1
    dc2, cc2 = conn.execute(
        "SELECT design_class, cell_count FROM runs WHERE run_id=?", (rid2,)).fetchone()
    assert cc2 is None                              # this run's cell_count honestly NULL
    assert dc2 == dc1, f"design_class flipped to {dc2!r} (bug #9 regression)"
