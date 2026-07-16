"""A/B causal-isolation, regression, no-op, staleness, and cross-check-cycle guards
(the 2026-07-15 issue-report engineer-loop fixes: P0-6, P0-11/12, P0-13, P1-13, P1-18)."""
import json

import ab_runner
import engineer_loop as el
import knowledge_db
import recipe_lifecycle


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _arm_dir(base, *, clock=None, die=None, sdc_period=None):
    """Materialize a minimal arm project with a config.mk (+ optional constraint.sdc)."""
    cons = base / "constraints"
    cons.mkdir(parents=True, exist_ok=True)
    lines = ["export DESIGN_NAME = d", "export CORE_UTILIZATION = 20"]
    if clock is not None:
        lines.append(f"export CLOCK_PERIOD = {clock}")
    if die is not None:
        lines.append(f"export DIE_AREA = {die}")
    (cons / "config.mk").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if sdc_period is not None:
        (cons / "constraint.sdc").write_text(
            f"create_clock -name clk -period {sdc_period} [get_ports clk]\n",
            encoding="utf-8")
    return str(base)


# ── P0-6: no-op / unknown-strategy park ──────────────────────────────────────

def test_known_apply_strategy_gate(tmp_path):
    conn = _conn(tmp_path)
    assert el._known_apply_strategy(conn, "density_relief") is True      # catalog
    assert el._known_apply_strategy(conn, "core_util_relief") is True    # backend
    assert el._known_apply_strategy(conn, "totally_made_up") is False    # fabricated
    # a strategy that ever produced a real fix_event has an application path
    conn.execute("INSERT INTO fix_events (fix_session_id, project_path, check_type, "
                 "iter, strategy, verdict) VALUES ('s','/p','drc',1,'learned_x','cleared')")
    conn.commit()
    assert el._known_apply_strategy(conn, "learned_x") is True


# ── P0-11 / P0-12: spec-equality (causal isolation) ──────────────────────────

def test_spec_mismatch_flags_relaxed_clock_on_signoff_arm(tmp_path):
    a = _arm_dir(tmp_path / "A", clock="10", die="0 0 100 100", sdc_period="10")
    b = _arm_dir(tmp_path / "B", clock="20", die="0 0 200 200", sdc_period="20")
    reason = el._arm_spec_mismatch(a, b, "drc")
    assert reason and reason.startswith("spec_mismatch:")
    assert "CLOCK_PERIOD" in reason and "DIE_AREA" in reason


def test_spec_match_passes(tmp_path):
    a = _arm_dir(tmp_path / "A", clock="10", die="0 0 100 100", sdc_period="10")
    b = _arm_dir(tmp_path / "B", clock="10", die="0 0 100 100", sdc_period="10")
    assert el._arm_spec_mismatch(a, b, "drc") is None


def test_spec_guard_exempts_timing_recipes(tmp_path):
    """A timing/place recipe legitimately moves a spec knob, so the guard is exempt."""
    a = _arm_dir(tmp_path / "A", clock="10")
    b = _arm_dir(tmp_path / "B", clock="20")
    assert el._arm_spec_mismatch(a, b, "timing") is None


# ── P0-13: regression guard (new DRC class) ──────────────────────────────────

def _seed_run_viol(conn, run_id, cats):
    conn.execute("INSERT OR REPLACE INTO runs (run_id, project_path, ingested_at) "
                 "VALUES (?,?,?)", (run_id, f"/p/{run_id}", "t"))
    conn.execute("INSERT OR REPLACE INTO run_violations (run_id, drc_status, "
                 "drc_categories_json, snapshot_ts) VALUES (?,?,?,?)",
                 (run_id, "fail", json.dumps(cats), "t"))
    conn.commit()


def test_regression_guard_detects_new_class(tmp_path):
    conn = _conn(tmp_path)
    _seed_run_viol(conn, "a", {"M1_SPACING": {"count": 1}})            # A: 1 residual
    _seed_run_viol(conn, "b", {"NEW_FATAL_SHORT": {"count": 8}})       # B: cleared M1, NEW short
    reason = el._ab_new_drc_regression(conn, "a", "b")
    assert reason and "NEW_FATAL_SHORT" in reason


def test_regression_guard_clean_when_no_new_class(tmp_path):
    conn = _conn(tmp_path)
    _seed_run_viol(conn, "a", {"M1_SPACING": {"count": 3}})
    _seed_run_viol(conn, "b", {"M1_SPACING": {"count": 1}})           # improved, no NEW class
    assert el._ab_new_drc_regression(conn, "a", "b") is None


# ── P1-18: cross-check repair-cycle detection ────────────────────────────────

def _seed_state(conn, run_id, ts, *, drc_cats, timing):
    conn.execute("INSERT OR REPLACE INTO runs (run_id, project_path, ingested_at) "
                 "VALUES (?,?,?)", (run_id, "/p/design", ts))
    conn.execute("INSERT OR REPLACE INTO run_violations (run_id, drc_status, "
                 "drc_categories_json, timing_tier, snapshot_ts) VALUES (?,?,?,?,?)",
                 (run_id, "fail", json.dumps(drc_cats), timing, ts))
    conn.commit()


def test_repair_cycle_detected_on_revisited_state(tmp_path):
    """DRC-clears-timing-breaks then timing-clears-DRC-breaks returns the design to its
    first global state -> a repair cycle."""
    conn = _conn(tmp_path)
    _seed_state(conn, "r1", "2026-06-10T00:00:00", drc_cats={"M2_SHORT": {"count": 2}}, timing="clean")
    _seed_state(conn, "r2", "2026-06-10T01:00:00", drc_cats={}, timing="violated")
    _seed_state(conn, "r3", "2026-06-10T02:00:00", drc_cats={"M2_SHORT": {"count": 2}}, timing="clean")
    assert el._detect_repair_cycle(conn, "/p/design") is not None


def test_no_cycle_on_monotonic_progress(tmp_path):
    conn = _conn(tmp_path)
    _seed_state(conn, "r1", "2026-06-10T00:00:00", drc_cats={"M2_SHORT": {"count": 5}}, timing="violated")
    _seed_state(conn, "r2", "2026-06-10T01:00:00", drc_cats={"M2_SHORT": {"count": 2}}, timing="minor")
    _seed_state(conn, "r3", "2026-06-10T02:00:00", drc_cats={}, timing="clean")
    assert el._detect_repair_cycle(conn, "/p/design") is None


# ── P1-13: regression auto-demotion is wired into the learner ────────────────

def test_learn_auto_demotes_regressed_promoted_recipe(tmp_path):
    import learn_heuristics
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    key = dict(symptom_id="symZ", design_class="crypto/small", platform="nangate45",
               strategy="density_relief")
    recipe_lifecycle.promote(conn, evidence="seed", **key)
    # two consecutive live regressions on this symptom+strategy
    for i in range(2):
        conn.execute(
            "INSERT INTO fix_events (fix_session_id, project_path, check_type, iter, "
            "strategy, verdict, symptom_id) VALUES (?,?,?,?,?,?,?)",
            (f"s{i}", "/p/d", "drc", 1, "density_relief", "regression", "symZ"))
    conn.commit()
    conn.close()
    assert recipe_lifecycle.get_status(knowledge_db.connect(db), **key) == "promoted"
    learn_heuristics.learn(db, tmp_path / "heuristics.json")
    assert recipe_lifecycle.get_status(knowledge_db.connect(db), **key) == "shadow"
