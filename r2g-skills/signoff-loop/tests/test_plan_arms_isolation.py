"""plan_arms_for_candidates must ISOLATE a candidate whose plan_trial raises.

Root cause (2026-06-28): `plan_trial` reads state that can race the campaign's concurrent
heuristics.json / ingest writes and raise transiently (observed: an intermittent
`KeyError 'design'`). `plan_arms_for_candidates` called it with NO try/except, so ONE
crashing candidate aborted the entire planning loop and stranded every candidate AFTER it.
`synth_memory_relax` (the LAST of 33 pending candidates) sat at 0 A/B trials for hours —
any transient crash earlier in the list blocked it on every drain, so it could never
promote. Fix: skip + log a crashing candidate (stays 'candidate', re-plans next drain),
never let it abort the loop.
"""
import engineer_loop as el


def test_plan_arms_isolates_a_crashing_candidate(tmp_path, monkeypatch):
    import ab_runner
    import recipe_lifecycle

    # a real subject dir for the GOOD candidate's arm copytree
    subj = tmp_path / "good_subject"
    (subj / "constraints").mkdir(parents=True)
    (subj / "constraints" / "config.mk").write_text("export DESIGN_NAME = g\n")

    bad = {"symptom_id": "badsym", "design_class": "c/l", "platform": "nangate45",
           "strategy": "strat_bad"}
    good = {"symptom_id": "goodsym", "design_class": "c/l", "platform": "nangate45",
            "strategy": "strat_good"}
    # bad is FIRST so, without isolation, its crash would abort before good is reached
    monkeypatch.setattr(recipe_lifecycle, "pending_candidates", lambda conn: [bad, good])
    monkeypatch.setattr(el, "_ab_coverage_gap", lambda conn, key: False)
    monkeypatch.setattr(el, "_symptom_check", lambda conn, sid, strat=None: "both")

    def fake_plan(conn, **k):
        if k["strategy"] == "strat_bad":
            raise KeyError("design")          # the transient race crash
        return {"designs": [{"design_name": "g", "project_path": str(subj),
                             "cell_count": 1}], "match_level": "exact"}
    monkeypatch.setattr(ab_runner, "plan_trial", fake_plan)

    led = el.Ledger(tmp_path / "l.jsonl")
    appended = el.plan_arms_for_candidates(led, conn=None, repeats=1)

    # the GOOD candidate (AFTER the crasher) still got its arms planned
    goods = [e for e in led.entries() if e.get("strategy") == "strat_good"]
    assert goods, "a crashing candidate aborted the loop and stranded the good candidate"
    assert {e["arm"] for e in goods} == {"A", "B"}    # both arms planned
    assert appended >= 2
    # the bad candidate planned nothing (skipped, not crashed)
    assert not [e for e in led.entries() if e.get("strategy") == "strat_bad"]


def test_plan_arms_sets_arm_platform_from_ab_key(tmp_path, monkeypatch):
    """2026-07-01: an A/B arm's on-disk config.mk PLATFORM must match ab_key.platform, NOT
    inherit the SUBJECT's stale platform. A nangate45 antenna candidate whose subject is a
    reused asap7 arm-scratch dir would otherwise run asap7.lydrc DRC on a nangate45 GDS and
    HANG, tail-blocking the wave. PLATFORM is ORFS ground truth (run_orfs builds against
    config.mk, never the passed arg)."""
    import re
    from pathlib import Path
    import ab_runner
    import recipe_lifecycle

    # subject with a STALE asap7 config.mk (e.g. reused arm-scratch from a prior round)
    subj = tmp_path / "stale_asap7_subject"
    (subj / "constraints").mkdir(parents=True)
    (subj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = s\nexport PLATFORM = asap7\n")

    cand = {"symptom_id": "sym", "design_class": "logic/small", "platform": "nangate45",
            "strategy": "antenna_diode_repair"}
    monkeypatch.setattr(recipe_lifecycle, "pending_candidates", lambda conn: [cand])
    monkeypatch.setattr(el, "_ab_coverage_gap", lambda conn, key: False)
    monkeypatch.setattr(el, "_symptom_check", lambda conn, sid, strat=None: "both")
    monkeypatch.setattr(ab_runner, "plan_trial", lambda conn, **k: {
        "designs": [{"design_name": "s", "project_path": str(subj), "cell_count": 1}],
        "match_level": "exact"})

    led = el.Ledger(tmp_path / "l.jsonl")
    el.plan_arms_for_candidates(led, conn=None, repeats=1)

    arms = [e for e in led.entries() if e.get("kind") == "ab_arm"]
    assert arms, "no arms planned"
    for a in arms:
        cfg = (Path(a["project_path"]) / "constraints" / "config.mk").read_text()
        m = re.search(r"(?m)^\s*(?:export\s+)?PLATFORM\s*=\s*(\S+)", cfg)
        assert m and m.group(1) == "nangate45", \
            f"arm {a['design']} inherited stale PLATFORM instead of ab_key: {cfg!r}"


def test_plan_arms_resets_arm_config_to_pre_recipe_baseline(tmp_path, monkeypatch):
    """P0-3 (recipe-lifecycle audit 2026-07-14, failure-patterns #48): a previously-FIXED
    subject carries the fixer's auto-block in config.mk. Both A/B arms must be materialized
    from the PRE-recipe baseline (auto-block stripped), so arm A is a clean control and
    arm B's forced recipe is not a no-op — otherwise the trial ties inconclusive and the
    causal reading is lost. Verify neither arm inherits the subject's post-repair
    CORE_UTILIZATION delta, and the baseline_config_sha is stamped on the ledger arm."""
    import re
    from pathlib import Path
    import ab_runner
    import recipe_lifecycle
    import diagnose_signoff_fix as dsf

    # subject as a previously-fixed design: authored CORE_UTILIZATION=40, then density_relief
    # lowered it to 17 IN the r2g auto-block (exactly how diagnose --apply writes it).
    subj = tmp_path / "fixed_subject"
    (subj / "constraints").mkdir(parents=True)
    base = "export DESIGN_NAME = s\nexport PLATFORM = nangate45\nexport CORE_UTILIZATION = 40\n"
    (subj / "constraints" / "config.mk").write_text(
        dsf.apply_edits(base, {"CORE_UTILIZATION": "17"}))
    # sanity: the subject really does carry the post-repair delta in the auto-block
    assert "= 17" in (subj / "constraints" / "config.mk").read_text()

    cand = {"symptom_id": "sym", "design_class": "logic/small", "platform": "nangate45",
            "strategy": "density_relief"}
    monkeypatch.setattr(recipe_lifecycle, "pending_candidates", lambda conn: [cand])
    monkeypatch.setattr(el, "_ab_coverage_gap", lambda conn, key: False)
    monkeypatch.setattr(el, "_symptom_check", lambda conn, sid, strat=None: "both")
    monkeypatch.setattr(ab_runner, "plan_trial", lambda conn, **k: {
        "designs": [{"design_name": "s", "project_path": str(subj), "cell_count": 1}],
        "match_level": "exact"})

    led = el.Ledger(tmp_path / "l.jsonl")
    el.plan_arms_for_candidates(led, conn=None, repeats=1)

    arms = [e for e in led.entries() if e.get("kind") == "ab_arm"]
    assert {a["arm"] for a in arms} == {"A", "B"}       # both arms planned
    for a in arms:
        cfg = (Path(a["project_path"]) / "constraints" / "config.mk").read_text()
        # the auto-block (and its post-repair CORE_UTILIZATION=17) is gone from BOTH arms
        assert dsf.BLOCK_START not in cfg, f"arm {a['design']} kept the auto-block: {cfg!r}"
        util = re.search(r"(?m)^\s*(?:export\s+)?CORE_UTILIZATION\s*=\s*(\S+)", cfg)
        assert util and util.group(1) == "40", \
            f"arm {a['design']} inherited post-repair util instead of baseline: {cfg!r}"
        assert a.get("baseline_config_sha"), "arm missing baseline_config_sha provenance"


def test_plan_arms_skips_missing_subject_dir(tmp_path, monkeypatch):
    """2026-07-03: a subject whose dir no longer exists (wiped round / clean-slate
    reset) must NOT become a ledger arm. The copytree guard `src.is_dir()` silently
    no-ops for a ghost subject but the arm entry was STILL appended -> the ghost arm
    flowed against a nonexistent project every drain, escalated place_arm_incomplete
    forever, and the candidate never validated. Defense-in-depth alongside the
    plan_trial Tier-1 isdir filter."""
    import ab_runner
    import recipe_lifecycle

    subj = tmp_path / "wiped_subject"                    # never created
    cand = {"symptom_id": "sym", "design_class": "logic/medium",
            "platform": "sky130hd", "strategy": "core_util_relief"}
    monkeypatch.setattr(recipe_lifecycle, "pending_candidates", lambda conn: [cand])
    monkeypatch.setattr(el, "_ab_coverage_gap", lambda conn, key: False)
    monkeypatch.setattr(el, "_symptom_check", lambda conn, sid, strat=None: "place")
    monkeypatch.setattr(ab_runner, "plan_trial", lambda conn, **k: {
        "designs": [{"design_name": "w", "project_path": str(subj), "cell_count": 1}],
        "match_level": "pooled_class"})

    led = el.Ledger(tmp_path / "l.jsonl")
    appended = el.plan_arms_for_candidates(led, conn=None, repeats=1)

    assert appended == 0
    assert not [e for e in led.entries() if e.get("kind") == "ab_arm"], \
        "ghost arm was ledger'd for a wiped subject"


def test_two_symptoms_same_subject_strategy_get_distinct_arms(tmp_path, monkeypatch):
    """2026-07-16 issue 7: two candidates sharing subject+platform+strategy but
    differing in symptom_id used to materialize IDENTICAL arm dir names — the
    second led.add merged onto the first pair, overwriting ab_key (evidence
    attributed to the wrong symptom, first candidate's experiment silently lost).
    Each trial's arm dirs now carry a per-trial hash; both experiments survive."""
    import ab_runner
    import recipe_lifecycle

    subj = tmp_path / "shared_subject"
    (subj / "constraints").mkdir(parents=True)
    (subj / "constraints" / "config.mk").write_text("export DESIGN_NAME = s\n")

    c1 = {"symptom_id": "symptom-one", "design_class": "crypto/small",
          "platform": "nangate45", "strategy": "density_relief"}
    c2 = {"symptom_id": "symptom-two", "design_class": "crypto/small",
          "platform": "nangate45", "strategy": "density_relief"}
    monkeypatch.setattr(recipe_lifecycle, "pending_candidates", lambda conn: [c1, c2])
    monkeypatch.setattr(el, "_ab_coverage_gap", lambda conn, key: False)
    monkeypatch.setattr(el, "_symptom_check", lambda conn, sid, strat=None: "both")
    monkeypatch.setattr(ab_runner, "plan_trial", lambda conn, **k: {
        "designs": [{"design_name": "s", "project_path": str(subj), "cell_count": 1}],
        "match_level": "exact"})

    led = el.Ledger(tmp_path / "l.jsonl")
    appended = el.plan_arms_for_candidates(led, conn=None, repeats=1)
    arms = [e for e in led.entries() if e.get("kind") == "ab_arm"]
    assert appended == 4                       # 2 candidates x arms A+B (k=1)
    assert len(arms) == 4                      # ...and ALL FOUR survive in the ledger
    assert len({e["design"] for e in arms}) == 4
    assert ({e["ab_key"]["symptom_id"] for e in arms}
            == {"symptom-one", "symptom-two"})
