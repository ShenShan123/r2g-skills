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


def test_localize_arm_sdc_repoints_to_local(tmp_path):
    """2026-06-25: an arm copy's config.mk SDC_FILE pins the ORIGINAL design's SDC, so the
    arm flows at the failing period and period_relax's SDC edit has NO effect (the 22f3e67
    SDC-pinning bug). _localize_arm_sdc repoints SDC_FILE at the arm's own constraint.sdc."""
    arm = tmp_path / "d_abB_periodre_0"
    (arm / "constraints").mkdir(parents=True)
    (arm / "constraints" / "constraint.sdc").write_text("set clk_period 15.0\n")
    (arm / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = d\n"
        "export SDC_FILE = /orig/d/constraints/constraint.sdc\n")
    engineer_loop._localize_arm_sdc(arm)
    cfg = (arm / "constraints" / "config.mk").read_text()
    assert str((arm / "constraints" / "constraint.sdc").resolve()) in cfg
    assert "/orig/d/constraints/constraint.sdc" not in cfg


def test_localize_arm_sdc_adds_when_absent(tmp_path):
    """If config.mk has no SDC_FILE line, _localize_arm_sdc appends one pointing local."""
    arm = tmp_path / "d_abA_periodre_0"
    (arm / "constraints").mkdir(parents=True)
    (arm / "constraints" / "constraint.sdc").write_text("set clk_period 9.0\n")
    (arm / "constraints" / "config.mk").write_text("export DESIGN_NAME = d\n")
    engineer_loop._localize_arm_sdc(arm)
    cfg = (arm / "constraints" / "config.mk").read_text()
    assert f"SDC_FILE = {(arm / 'constraints' / 'constraint.sdc').resolve()}" in cfg


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


def test_apply_recipe_strategy_place_lowers_existing_util(tmp_path):
    """2026-06-26: when the subject ALREADY auto-sizes (CORE_UTILIZATION set), the
    core_util_relief place arm must LOWER it so arm B diverges from the arm-A control.
    Before this, _resize_to_core_util no-opped (CORE_UTILIZATION already present), so both
    arms ran util=20 -> identical outcome -> inconclusive forever -> the place class never
    promoted and the loop stalled (promo_ng flat for 8 waves)."""
    import re
    proj = tmp_path / "d_abB"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = d\nexport CORE_UTILIZATION = 20\n", encoding="utf-8")
    engineer_loop._apply_recipe_strategy(
        {"project_path": str(proj), "strategy": "core_util_relief"})
    cfg = (proj / "constraints" / "config.mk").read_text()
    m = re.search(r"CORE_UTILIZATION\s*=\s*(\d+)", cfg)
    assert m and int(m.group(1)) < 20      # arm B lowered util -> diverges from control


def test_lower_core_util_floor_is_honest_noop(tmp_path):
    """At/below the floor there is no relief left: _lower_core_util returns False (an
    honest non-divergent arm) instead of dropping util into the basement."""
    proj = tmp_path / "d_abB"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        f"export CORE_UTILIZATION = {engineer_loop._CORE_UTIL_FLOOR}\n", encoding="utf-8")
    assert engineer_loop._lower_core_util({"project_path": str(proj)}) is False


# ── PPL-0024 pin-overflow recovery (2026-06-26: the dominant mislabeled 'unseen_crash') ──

def test_is_ppl0024_reads_flow_log(tmp_path):
    proj = tmp_path / "p"
    run = proj / "backend" / "RUN_X"
    run.mkdir(parents=True)
    (run / "flow.log").write_text("[ERROR PPL-0024] Number of IO pins (373) exceeds ...\n")
    assert engineer_loop._is_ppl0024({"project_path": str(proj)}) is True
    (run / "flow.log").write_text("[ERROR GRT-0001] something else\n")
    assert engineer_loop._is_ppl0024({"project_path": str(proj)}) is False


def test_relieve_pin_overflow_enlarges_die(tmp_path):
    """Auto-sized subject -> lower util (bigger core -> more perimeter); fixed-die subject
    -> convert to a low CORE_UTILIZATION so ORFS auto-sizes a larger die."""
    auto = tmp_path / "auto"
    (auto / "constraints").mkdir(parents=True)
    (auto / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 25\n")
    assert engineer_loop._relieve_pin_overflow({"project_path": str(auto)}) is True
    import re
    m = re.search(r"CORE_UTILIZATION\s*=\s*(\d+)",
                  (auto / "constraints" / "config.mk").read_text())
    assert m and int(m.group(1)) < 25                       # die enlarged

    fixed = tmp_path / "fixed"
    (fixed / "constraints").mkdir(parents=True)
    (fixed / "constraints" / "config.mk").write_text(
        "export DIE_AREA = 0 0 50 50\nexport CORE_AREA = 5 5 45 45\n")
    assert engineer_loop._relieve_pin_overflow({"project_path": str(fixed)}) is True
    cfg = (fixed / "constraints" / "config.mk").read_text()
    assert "CORE_UTILIZATION" in cfg and "DIE_AREA" not in cfg


def test_process_one_recovers_ppl0024_then_honest_residual(tmp_path, monkeypatch):
    """A PPL-0024 place abort routes through pin-overflow recovery: util is lowered (bigger
    die) and the flow retried. If it STILL aborts, it escalates as the honest
    'pin_overflow_residual' -- NEVER 'unseen_crash' (the mislabel the 2026-06-26 audit found)."""
    import knowledge_db
    conn = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(conn)
    proj = tmp_path / "pinheavy"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = d\nexport CORE_UTILIZATION = 25\n", encoding="utf-8")
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    led.add({"design": "pinheavy", "project_path": str(proj), "platform": "nangate45"})
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: 1)         # always aborts
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: None)
    monkeypatch.setattr(engineer_loop, "_fail_stage", lambda e: "place")
    monkeypatch.setattr(engineer_loop, "_is_flw0024", lambda e: False)
    monkeypatch.setattr(engineer_loop, "_is_ppl0024", lambda e: True)
    monkeypatch.setattr(engineer_loop, "_record_resize_fix", lambda e, *, cleared: None)
    out = engineer_loop.process_one(led, led.pending()[0], conn=conn)
    assert out == "escalated"
    import re
    m = re.search(r"CORE_UTILIZATION\s*=\s*(\d+)",
                  (proj / "constraints" / "config.mk").read_text())
    assert m and int(m.group(1)) < 25                       # relief WAS applied
    reason = conn.execute(
        "SELECT reason FROM escalations WHERE design='pinheavy'").fetchone()
    assert reason and reason[0] == "pin_overflow_residual"   # honest label, not unseen_crash


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


def test_arm_metric_timing_ondisk_fallback(tmp_path):
    """2026-06-25: when a --check timing reflow leaves the runs row's wns_ns/timing_tier
    NULL, _arm_metric(timing=True) falls back to the arm's ON-DISK timing verdict
    (timing_check.json tier / ppa.json setup_wns) so a genuinely-closed arm is not judged
    a failure (the bug that kept every live timing trial inconclusive)."""
    conn = _conn(tmp_path)
    met, miss = tmp_path / "met", tmp_path / "miss"
    (met / "reports").mkdir(parents=True)
    (met / "reports" / "timing_check.json").write_text(json.dumps({"tier": "clean"}))
    (miss / "reports").mkdir(parents=True)
    (miss / "reports" / "ppa.json").write_text(
        json.dumps({"summary": {"timing": {"setup_wns": -2.1}}}))
    for pp in (met, miss):
        conn.execute("INSERT INTO runs (run_id, project_path, ingested_at, orfs_status, "
                     "timing_tier, wns_ns) VALUES (?,?,?,?,NULL,NULL)",
                     (str(pp), str(pp), "2026-06-25T00:00:00Z", "pass"))
    conn.commit()
    assert engineer_loop._arm_metric(conn, str(met), timing=True)["is_success"] is True
    assert engineer_loop._arm_metric(conn, str(miss), timing=True)["is_success"] is False


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
