"""2026-06-24 loop-closure: close the inert TIMING + PLACE A/B classes.

Before this, _symptom_check routed every non-route symptom to '--check both' (DRC/LVS),
so a period_relax (timing) or core_util_relief (place) recipe's two arms did
byte-identical work -> inconclusive forever -> never promoted, while burning a full
multi-hour signoff per repeat (the campaign stall). These tests lock:
  - strategy-based routing (place -> 'place', timing -> 'timing');
  - place arms run the apply-then-flow backend-abort runner (arm B resizes the die);
  - a timing arm drives fix_signoff --check timing and judges on the timing verdict;
  - the coverage guard skips arms that cannot diverge (no demote).
"""
import json
from pathlib import Path

import ab_runner
import engineer_loop
import knowledge_db
import recipe_lifecycle


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


# ── strategy-based routing ───────────────────────────────────────────────────

def test_symptom_check_routes_by_strategy(tmp_path):
    conn = _conn(tmp_path)
    assert engineer_loop._symptom_check(conn, None, "core_util_relief") == "place"
    assert engineer_loop._symptom_check(conn, None, "period_relax") == "timing"
    assert engineer_loop._symptom_check(conn, None, "utilization_reduce") == "timing"
    # A DRC/LVS strategy with no special routing stays on the signoff path.
    assert engineer_loop._symptom_check(conn, None, "antenna_diode_repair") == "both"
    assert engineer_loop._symptom_check(conn, None, None) == "both"


def test_symptom_check_route_symptom_still_routes(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO symptoms (symptom_id, check_type, class) "
                 "VALUES ('s1','orfs_stage','route')")
    conn.execute("INSERT INTO symptoms (symptom_id, check_type, class) "
                 "VALUES ('s2','orfs_stage','place')")
    conn.commit()
    assert engineer_loop._symptom_check(conn, "s1") == "route"
    assert engineer_loop._symptom_check(conn, "s2") == "place"


# ── place arm: apply-then-flow backend-abort runner ──────────────────────────

def test_place_arm_routes_to_backend_runner(tmp_path, monkeypatch):
    """A place ab_arm (check='place') must go through the apply-then-flow runner, NOT
    the signoff flow->fix path (place aborts before signoff exists)."""
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    led.add({"design": "d_abB_coreutil_0", "project_path": str(tmp_path / "d_abB"),
             "platform": "nangate45", "kind": "ab_arm", "arm": "B",
             "strategy": "core_util_relief", "check": "place"})
    seen = {}
    monkeypatch.setattr(engineer_loop, "_process_backend_ab_arm",
                        lambda led, e, conn: seen.update(called=True, check=e["check"]))
    out = engineer_loop.process_one(led, led.pending()[0], conn=None)
    assert out is None and seen == {"called": True, "check": "place"}


def test_apply_recipe_strategy_place_resizes(tmp_path):
    """arm B of a place trial converts the too-small fixed DIE_AREA -> CORE_UTILIZATION
    (the FLW-0024 recovery) so its place stage completes where arm A's aborts."""
    proj = tmp_path / "d_abB"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = d\nexport DIE_AREA = 0 0 50 50\n"
        "export CORE_AREA = 5 5 45 45\n", encoding="utf-8")
    engineer_loop._apply_recipe_strategy(
        {"project_path": str(proj), "strategy": "core_util_relief"})
    cfg = (proj / "constraints" / "config.mk").read_text()
    assert "CORE_UTILIZATION" in cfg and "DIE_AREA" not in cfg


# ── timing arm: drives fix_signoff --check timing ────────────────────────────

def test_timing_arm_drives_check_timing(tmp_path, monkeypatch):
    """A timing ab_arm reaches a completed flow whose timing misses, so it falls through
    to the signoff path and MUST invoke the fixer with --check timing (not 'both')."""
    proj = tmp_path / "t_abB"
    run = proj / "backend" / "RUN_X"
    run.mkdir(parents=True)
    (run / "stage_log.jsonl").write_text(
        "\n".join(json.dumps({"stage": s, "status": 0})
                  for s in ("synth", "floorplan", "place", "cts", "route", "finish")) + "\n")
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    led.add({"design": "t_abB_periodre_0", "project_path": str(proj),
             "platform": "nangate45", "kind": "ab_arm", "arm": "B",
             "strategy": "period_relax", "check": "timing"})
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: 0)
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: None)
    seen = {}
    monkeypatch.setattr(engineer_loop, "_run_fix",
                        lambda e: (seen.update(check=e.get("check")) or 0))
    engineer_loop.process_one(led, led.pending()[0], conn=None)
    assert seen["check"] == "timing"


def test_arm_metric_timing_uses_timing_tier(tmp_path):
    """A timing arm judges on the ingested timing verdict (wns_ns/timing_tier), NOT the
    generic is_success — a timing miss never aborts the flow, so both arms reach a GDS."""
    conn = _conn(tmp_path)
    for pp, tier, wns in (("/p/met", "clean", 0.4), ("/p/miss", "severe", -3.2)):
        conn.execute(
            "INSERT INTO runs (run_id, project_path, ingested_at, orfs_status, "
            "timing_tier, wns_ns) VALUES (?,?,?,?,?,?)",
            (pp, pp, "2026-06-24T00:00:00Z", "pass", tier, wns))
    conn.commit()
    met = engineer_loop._arm_metric(conn, "/p/met", timing=True)
    miss = engineer_loop._arm_metric(conn, "/p/miss", timing=True)
    assert met["is_success"] is True and miss["is_success"] is False
    # judge: arm B (relaxed -> met) beats arm A (original -> miss).
    assert ab_runner.judge_repeated([miss, miss], [met, met]) == "win"


# ── coverage guard: skip arms that cannot diverge, NEVER demote ──────────────

def test_coverage_gap_nondivergent_strategy(tmp_path):
    conn = _conn(tmp_path)
    assert engineer_loop._ab_coverage_gap(
        conn, {"symptom_id": "s", "design_class": "logic/small",
               "platform": "sky130hd", "strategy": "lvs_resolve_unknown"}) is True
    assert engineer_loop._ab_coverage_gap(
        conn, {"symptom_id": "s", "design_class": "logic/small",
               "platform": "sky130hd", "strategy": "antenna_diode_repair"}) is False


def test_coverage_gap_inconclusive_backoff(tmp_path):
    conn = _conn(tmp_path)
    key = {"symptom_id": "sx", "design_class": "logic/small",
           "platform": "nangate45", "strategy": "antenna_diode_repair"}
    for _ in range(engineer_loop.AB_INCONCLUSIVE_MAX):
        ab_runner.record_trial(conn, key=key, verdict="inconclusive",
                               arm_a_run_id=None, arm_b_run_id=None, metrics={})
    assert engineer_loop._ab_coverage_gap(conn, key) is True
    # a single decisive verdict clears the backoff (the recipe IS learnable)
    ab_runner.record_trial(conn, key=key, verdict="win",
                           arm_a_run_id=None, arm_b_run_id=None, metrics={})
    assert engineer_loop._ab_coverage_gap(conn, key) is False


def test_plan_arms_skips_coverage_gap_without_demote(tmp_path, monkeypatch):
    """plan_arms_for_candidates skips a non-divergent candidate (0 arms) + opens an
    ab_coverage_gap escalation, and leaves it 'candidate' (never shadow)."""
    conn = _conn(tmp_path)
    key = dict(symptom_id="sl", design_class="logic/small",
               platform="sky130hd", strategy="lvs_resolve_unknown")
    recipe_lifecycle.enqueue_candidate(conn, **key)
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    # plan_trial must NOT be reached for a guarded candidate.
    monkeypatch.setattr(ab_runner, "plan_trial",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("planned")))
    appended = engineer_loop.plan_arms_for_candidates(led, conn)
    assert appended == 0
    assert recipe_lifecycle.get_status(conn, **key) == "candidate"   # NOT demoted
    n = conn.execute("SELECT COUNT(*) FROM escalations WHERE reason='ab_coverage_gap'"
                     ).fetchone()[0]
    assert n == 1
