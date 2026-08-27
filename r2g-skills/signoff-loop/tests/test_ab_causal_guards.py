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


# ── 2026-07-16 issue 3: full-config baseline-divergence veto ─────────────────

def test_baseline_divergence_flags_extra_knobs(tmp_path):
    """The P0-11 spec guard only compares clock/die/core+SDC; an UNRELATED knob
    smuggled into one arm's baseline region must now veto (2026-07-16 issue 3)."""
    a = _arm_dir(tmp_path / "d_abA_density__0", clock="10", die="0 0 100 100",
                 sdc_period="10")
    b = _arm_dir(tmp_path / "d_abB_density__0", clock="10", die="0 0 100 100",
                 sdc_period="10")
    cfg = tmp_path / "d_abB_density__0" / "constraints" / "config.mk"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + "export PLACE_DENSITY_LB_ADDON = 0.01\nexport ABC_AREA = 0\n",
                   encoding="utf-8")
    reason = el._arm_baseline_divergence(a, b, "drc")
    assert reason and reason.startswith("baseline_divergence:")
    assert "PLACE_DENSITY_LB_ADDON" in reason and "ABC_AREA" in reason
    # identical baselines pass
    assert el._arm_baseline_divergence(a, a, "drc") is None


def test_baseline_divergence_ignores_auto_block(tmp_path):
    """Arm edits inside the marked signoff-fix auto-block are the LEGITIMATE
    divergence surface (arm A control vs arm B forced recipe) — never a veto."""
    import diagnose_signoff_fix as dsf
    a = _arm_dir(tmp_path / "d_abA_density__0", clock="10", sdc_period="10")
    b = _arm_dir(tmp_path / "d_abB_density__0", clock="10", sdc_period="10")
    cfg = tmp_path / "d_abB_density__0" / "constraints" / "config.mk"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + f"{dsf.BLOCK_START}\nexport CORE_UTILIZATION = 5\n{dsf.BLOCK_END}\n",
                   encoding="utf-8")
    assert el._arm_baseline_divergence(a, b, "drc") is None


def test_baseline_divergence_normalizes_arm_local_paths(tmp_path):
    """Each arm's SDC_FILE legitimately points into its OWN dir (_localize_arm_sdc);
    the per-arm dir name in a value must not read as divergence."""
    a = _arm_dir(tmp_path / "d_abA_density__0", clock="10")
    b = _arm_dir(tmp_path / "d_abB_density__0", clock="10")
    for p in (tmp_path / "d_abA_density__0", tmp_path / "d_abB_density__0"):
        cfg = p / "constraints" / "config.mk"
        cfg.write_text(cfg.read_text(encoding="utf-8")
                       + f"export SDC_FILE = {p}/constraints/constraint.sdc\n",
                       encoding="utf-8")
    assert el._arm_baseline_divergence(str(tmp_path / "d_abA_density__0"),
                                       str(tmp_path / "d_abB_density__0"),
                                       "drc") is None


def test_baseline_divergence_exempts_place_and_timing(tmp_path):
    """place/synth relief writes BARE exports outside the block by design, and
    timing arms edit the SDC — same scoping as the spec guard."""
    a = _arm_dir(tmp_path / "A", clock="10")
    b = _arm_dir(tmp_path / "B", clock="10")
    cfg = tmp_path / "B" / "constraints" / "config.mk"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + "export CORE_UTILIZATION = 30\n", encoding="utf-8")
    assert el._arm_baseline_divergence(a, b, "place") is None
    assert el._arm_baseline_divergence(a, b, "timing") is None


# ── 2026-07-16 issue 4: global cross-check regression veto ───────────────────

def _seed_run_status(conn, run_id, *, orfs=None, drc=None, lvs=None, tier=None):
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, project_path, ingested_at, "
        "orfs_status, drc_status, lvs_status, timing_tier) VALUES (?,?,?,?,?,?,?)",
        (run_id, f"/p/{run_id}", "t", orfs, drc, lvs, tier))
    conn.commit()


def test_global_regression_vetoes_lvs_break(tmp_path):
    conn = _conn(tmp_path)
    _seed_run_status(conn, "a", lvs="clean", tier="clean")
    _seed_run_status(conn, "b", lvs="fail", tier="clean")
    reason = el._ab_global_regression(conn, "a", "b")
    assert reason and "lvs_regression:clean->fail" in reason


def test_global_regression_vetoes_timing_break(tmp_path):
    conn = _conn(tmp_path)
    _seed_run_status(conn, "a", lvs="clean", tier="clean")
    _seed_run_status(conn, "b", lvs="clean", tier="severe")
    reason = el._ab_global_regression(conn, "a", "b")
    assert reason and "timing_regression:clean->severe" in reason
    # losing the constraint entirely (unconstrained) ranks as severe
    _seed_run_status(conn, "b2", lvs="clean", tier="unconstrained")
    assert "timing_regression" in (el._ab_global_regression(conn, "a", "b2") or "")


def test_global_regression_vetoes_disappeared_check(tmp_path):
    """A check arm A definitively ran that is MISSING in B = disabled, not passed."""
    conn = _conn(tmp_path)
    _seed_run_status(conn, "a", lvs="clean")
    _seed_run_status(conn, "b", lvs=None)
    assert "check_missing:lvs" in (el._ab_global_regression(conn, "a", "b") or "")


def test_global_regression_silent_when_no_positive_signal(tmp_path):
    """No POSITIVE good->bad flip -> no veto: neutral/absent statuses carry no
    signal, and an improvement is never a regression."""
    conn = _conn(tmp_path)
    _seed_run_status(conn, "a", lvs="skipped", tier="unknown")
    _seed_run_status(conn, "b", lvs="fail", tier="severe")     # A had no good state
    assert el._ab_global_regression(conn, "a", "b") is None
    _seed_run_status(conn, "a2", lvs="fail", tier="severe")
    _seed_run_status(conn, "b2", lvs="clean", tier="clean")    # improvement
    assert el._ab_global_regression(conn, "a2", "b2") is None
    assert el._ab_global_regression(conn, None, "b2") is None  # unresolvable arm


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


# ── P1-R2 (2026-07-19 audit, failure-patterns #52): magnitudes count ─────────

def test_quantitative_progress_is_not_a_cycle(tmp_path):
    """The fingerprint kept only the SET of nonzero DRC classes, so the real
    wbuart32 pair — same M1_SPACING class at counts 100 and 10 — fingerprinted
    identically and a 90% reduction was escalated as repair_cycle_nonconverged,
    stopping a campaign that was converging."""
    conn = _conn(tmp_path)
    _seed_state(conn, "r1", "2026-06-10T00:00:00",
                drc_cats={"M1_SPACING": {"count": 100}}, timing="clean")
    _seed_state(conn, "r2", "2026-06-10T01:00:00",
                drc_cats={"M1_SPACING": {"count": 10}}, timing="clean")
    assert el._detect_repair_cycle(conn, "/p/design") is None


def test_immaterial_change_is_still_a_cycle(tmp_path):
    """The tolerance is a factor of ~2: 100 -> 95 is noise, not progress, so a
    genuine ping-pong is still caught. Raw counts would have lost this."""
    conn = _conn(tmp_path)
    _seed_state(conn, "r1", "2026-06-10T00:00:00",
                drc_cats={"M1_SPACING": {"count": 100}}, timing="clean")
    _seed_state(conn, "r2", "2026-06-10T01:00:00",
                drc_cats={}, timing="violated")
    _seed_state(conn, "r3", "2026-06-10T02:00:00",
                drc_cats={"M1_SPACING": {"count": 95}}, timing="clean")
    assert el._detect_repair_cycle(conn, "/p/design") is not None


def test_regression_in_magnitude_is_a_new_state_not_a_repeat(tmp_path):
    """Getting WORSE is also not a revisit of the better state."""
    conn = _conn(tmp_path)
    _seed_state(conn, "r1", "2026-06-10T00:00:00",
                drc_cats={"M1_SPACING": {"count": 10}}, timing="clean")
    _seed_state(conn, "r2", "2026-06-10T01:00:00",
                drc_cats={"M1_SPACING": {"count": 400}}, timing="clean")
    assert el._detect_repair_cycle(conn, "/p/design") is None


# ── P1-13: regression auto-demotion is wired into the learner ────────────────

def _seed_regressions(conn, *, project, platform, design_class, n=2,
                      provenance="live", symptom="symZ",
                      strategy="density_relief"):
    """n live regression fix_events on `project`, whose latest ingested run
    carries `platform`/`design_class` (the exact-domain join surface)."""
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, project_path, ingested_at, platform, "
        "design_class) VALUES (?,?,?,?,?)",
        (f"run:{project}", project, "2026-07-16T00:00:00", platform, design_class))
    for i in range(n):
        conn.execute(
            "INSERT INTO fix_events (fix_session_id, project_path, check_type, iter, "
            "strategy, verdict, symptom_id, platform, provenance) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (f"s{project}{i}", project, "drc", 1, strategy, "regression", symptom,
             platform, provenance))
    conn.commit()


def test_learn_auto_demotes_regressed_promoted_recipe(tmp_path):
    import learn_heuristics
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    key = dict(symptom_id="symZ", design_class="crypto/small", platform="nangate45",
               strategy="density_relief")
    recipe_lifecycle.promote(conn, evidence="seed", **key)
    # two consecutive live regressions IN THE KEY'S OWN DOMAIN
    _seed_regressions(conn, project="/p/d", platform="nangate45",
                      design_class="crypto/small")
    conn.close()
    assert recipe_lifecycle.get_status(knowledge_db.connect(db), **key) == "promoted"
    learn_heuristics.learn(db, tmp_path / "heuristics.json")
    assert recipe_lifecycle.get_status(knowledge_db.connect(db), **key) == "shadow"


# ── 2026-07-16 issue 2: tied corpus is order-independent ─────────────────────

_TIE_KEY = dict(symptom_id="symT", design_class="logic/small",
                platform="nangate45", strategy="density_relief")


def _seed_arm_runs(conn, subject):
    """Two REAL arm runs for `subject` so the trial is provenance-verifiable."""
    for arm in ("A", "B"):
        rid = f"run:{subject}:{arm}"
        conn.execute("INSERT OR REPLACE INTO runs (run_id, project_path, "
                     "ingested_at) VALUES (?,?,?)",
                     (rid, f"/p/{subject}_ab{arm}_density__0", "t"))
    conn.commit()
    return f"run:{subject}:A", f"run:{subject}:B"


def _record(conn, verdict, subject):
    a, b = _seed_arm_runs(conn, subject)
    ab_runner.record_trial(conn, key=dict(_TIE_KEY), verdict=verdict,
                           arm_a_run_id=a, arm_b_run_id=b, metrics={},
                           trial_uuid=f"{subject}:{verdict}")


def test_tied_corpus_is_order_independent(tmp_path):
    """win-then-loss used to stay 'promoted' while loss-then-win stayed 'shadow'
    from the SAME net corpus (2026-07-16 issue 2). Both orders must now land in
    the same deterministic state: candidate (tied evidence = re-validate)."""
    finals = []
    for order in (("win", "loss"), ("loss", "win")):
        conn = _conn(tmp_path / order[0])
        recipe_lifecycle.enqueue_candidate(conn, **_TIE_KEY)
        for verdict, subject in zip(order, ("s1", "s2")):
            _record(conn, verdict, subject)
        finals.append(recipe_lifecycle.get_status(conn, **_TIE_KEY))
        conn.close()
    assert finals[0] == finals[1] == "candidate"


def test_tie_neutralizes_transient_promotion(tmp_path):
    """One decisive win promotes; the tie-ing loss must take the promotion BACK."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **_TIE_KEY)
    _record(conn, "win", "s1")
    assert recipe_lifecycle.get_status(conn, **_TIE_KEY) == "promoted"
    _record(conn, "loss", "s2")
    assert recipe_lifecycle.get_status(conn, **_TIE_KEY) == "candidate"
    # and a later decisive win re-resolves the tie forward
    _record(conn, "win", "s3")
    assert recipe_lifecycle.get_status(conn, **_TIE_KEY) == "promoted"


def test_no_decisive_evidence_still_leaves_status_unchanged(tmp_path):
    """wins==losses==0 stays a no-op: inconclusives never transition (bug #2)."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **_TIE_KEY)
    _record(conn, "inconclusive", "s1")
    assert recipe_lifecycle.get_status(conn, **_TIE_KEY) == "candidate"


# ── 2026-07-16 issue 8: regression demotion is exact-domain scoped ────────────

def test_auto_demote_ignores_cross_platform_regressions(tmp_path):
    """asap7/cpu failures must not disable the nangate45/crypto recipe they never
    touched (2026-07-16 issue 8) — cross-domain evidence is transfer signal only."""
    conn = _conn(tmp_path)
    key = dict(symptom_id="symZ", design_class="crypto/small", platform="nangate45",
               strategy="density_relief")
    recipe_lifecycle.promote(conn, evidence="seed", **key)
    _seed_regressions(conn, project="/p/foreign", platform="asap7",
                      design_class="cpu/large")
    assert ab_runner.auto_demote_on_regression(conn, key=key) is False
    assert recipe_lifecycle.get_status(conn, **key) == "promoted"
    # same platform but different design class: still out of domain
    _seed_regressions(conn, project="/p/otherclass", platform="nangate45",
                      design_class="logic/large")
    assert ab_runner.auto_demote_on_regression(conn, key=key) is False
    # exact domain: demotes
    _seed_regressions(conn, project="/p/exact", platform="nangate45",
                      design_class="crypto/small")
    assert ab_runner.auto_demote_on_regression(conn, key=key) is True
    assert recipe_lifecycle.get_status(conn, **key) == "shadow"


def test_auto_demote_ignores_backfilled_regressions(tmp_path):
    """A backfilled historical import is not a LIVE regression (spec §7)."""
    conn = _conn(tmp_path)
    key = dict(symptom_id="symZ", design_class="crypto/small", platform="nangate45",
               strategy="density_relief")
    recipe_lifecycle.promote(conn, evidence="seed", **key)
    _seed_regressions(conn, project="/p/hist", platform="nangate45",
                      design_class="crypto/small", provenance="backfill:import")
    assert ab_runner.auto_demote_on_regression(conn, key=key) is False
    assert recipe_lifecycle.get_status(conn, **key) == "promoted"
