"""PPL-0024 pin-overflow: the relief lever must size the die to the PERIMETER the IO
placer DEMANDS (its own stated target), not a cell-area CORE_UTILIZATION step that
undershoots cell-tiny/pin-huge designs.

This was the wrong-lever bug (2026-06-27 audit) that tied EVERY nangate45 core_util_relief
A/B trial inconclusive: the subjects are pin-bound (1521-3089 IO pins, cell-tiny), the placer
error literally says "Increase the die perimeter from <A>um to <B>um", but a fixed 0.6x util
step grew the die from CELL area (ip_demux util 12 -> perimeter 631um where the placer demanded
851.76um), so BOTH arms PPL-0024-aborted identically -> no decisive verdict -> no nangate45
recipe ever promoted (promo_ng flat at 0; the stale-noise promotion was correctly reverted).
"""
import re

import engineer_loop as el

# The exact placer message wording (note: no space between number and "um", and "the").
_PPL = ("[ERROR PPL-0024] Number of IO pins (1521) exceeds maximum number of available "
        "positions (1112). Increase the die perimeter from 631.18um to 851.76um.\n")


def _seed_ppl(proj, msg=_PPL):
    run = proj / "backend" / "RUN_2026-06-27_00-00-00"
    run.mkdir(parents=True)
    (run / "flow.log").write_text(msg)


def _seed_recorded_pin_fix(proj, util="12", target=851.76):
    """Turn a failing subject into the exact post-live-fix shape selected for A/B."""
    cfg = proj / "constraints" / "config.mk"
    cfg.write_text(f"export CORE_UTILIZATION = {util}\n")
    before = el._config_snapshot({"project_path": str(proj)})
    assert el._set_explicit_die(str(proj), target)
    entry = {
        "project_path": str(proj),
        "_r2g_config_effect": el._config_effect(
            before, el._config_snapshot({"project_path": str(proj)})),
    }
    el._record_pin_perimeter_fix(entry, cleared=True)


def _core_perimeter(cfg_text):
    m = re.search(r"CORE_AREA = \d+ \d+ (\d+) \d+", cfg_text)
    assert m, f"no square CORE_AREA in:\n{cfg_text}"
    return 4 * (int(m.group(1)) - el._PIN_CORE_INSET_UM)


# ── _ppl0024_required_perimeter: parse the placer's stated target ────────────
def test_required_perimeter_parses_target(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    _seed_ppl(proj)
    assert el._ppl0024_required_perimeter(str(proj)) == 851.76


def test_required_perimeter_none_without_ppl(tmp_path):
    proj = tmp_path / "p"
    run = proj / "backend" / "RUN_A"
    run.mkdir(parents=True)
    (run / "flow.log").write_text("[ERROR GRT-0001] routing congestion\n")
    assert el._ppl0024_required_perimeter(str(proj)) is None
    # no backend at all -> None, never a crash
    assert el._ppl0024_required_perimeter(str(tmp_path / "absent")) is None


# ── _set_explicit_die: perimeter-targeted die, hard-rule knob untouched ──────
def test_set_explicit_die_meets_perimeter(tmp_path):
    proj = tmp_path / "p"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = d\nexport CORE_UTILIZATION = 12\n"
        "export PLACE_DENSITY_LB_ADDON = 0.2\n")
    assert el._set_explicit_die(str(proj), 851.76) is True
    cfg = (proj / "constraints" / "config.mk").read_text()
    assert "CORE_UTILIZATION" not in cfg                 # cell-area lever dropped
    assert "PLACE_DENSITY_LB_ADDON = 0.2" in cfg         # hard-rule floor untouched
    assert _core_perimeter(cfg) >= 851.76                # core MEETS the placer's demand
    md = re.search(r"DIE_AREA = 0 0 (\d+) \d+", cfg)     # die strictly contains the core
    mc = re.search(r"CORE_AREA = \d+ \d+ (\d+) \d+", cfg)
    assert md and mc and int(md.group(1)) > int(mc.group(1))


def test_set_explicit_die_noop_without_target(tmp_path):
    proj = tmp_path / "p"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 12\n")
    assert el._set_explicit_die(str(proj), None) is False
    assert el._set_explicit_die(str(proj), 0) is False
    assert "CORE_UTILIZATION = 12" in (proj / "constraints" / "config.mk").read_text()


# ── _relieve_pin_overflow: prefer perimeter die, fall back to util lever ─────
def test_relieve_prefers_perimeter_die_from_disk(tmp_path):
    """A PPL-0024 message on disk -> size an explicit perimeter die, NOT the util lever that
    undershoots (the live tie: util=12 reached 631um, placer demanded 851.76um)."""
    proj = tmp_path / "p"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 12\n")
    _seed_ppl(proj)
    assert el._relieve_pin_overflow({"project_path": str(proj)}) is True
    cfg = (proj / "constraints" / "config.mk").read_text()
    assert "DIE_AREA" in cfg and "CORE_UTILIZATION" not in cfg
    assert _core_perimeter(cfg) >= 851.76


def test_relieve_explicit_target_overrides(tmp_path):
    """The A/B arm passes the SUBJECT's perimeter (the arm copy excludes the subject backend)."""
    proj = tmp_path / "p"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 18\n")
    assert el._relieve_pin_overflow(
        {"project_path": str(proj)}, perimeter_target=1729.84) is True
    assert _core_perimeter((proj / "constraints" / "config.mk").read_text()) >= 1729.84


def test_relieve_falls_back_to_util_lever(tmp_path):
    """No parseable perimeter (e.g. an FLW-0024 over-pack) -> the util lever, preserving the
    existing FLW-0024 behavior the prior tests assert."""
    proj = tmp_path / "p"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 25\n")
    assert el._relieve_pin_overflow({"project_path": str(proj)}) is True
    cfg = (proj / "constraints" / "config.mk").read_text()
    m = re.search(r"CORE_UTILIZATION\s*=\s*(\d+)", cfg)
    assert m and int(m.group(1)) < 25 and "DIE_AREA" not in cfg


# ── _apply_recipe_strategy (arm B): use the stamped pin target ───────────────
def test_apply_recipe_strategy_uses_pin_target(tmp_path):
    proj = tmp_path / "d_abB"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 12\n")
    el._apply_recipe_strategy(
        {"project_path": str(proj), "strategy": "pin_perimeter_floor",
         "pin_perimeter_target": 851.76})
    cfg = (proj / "constraints" / "config.mk").read_text()
    assert "DIE_AREA" in cfg and "CORE_UTILIZATION" not in cfg
    assert _core_perimeter(cfg) >= 851.76


def test_pin_perimeter_recipe_without_target_is_honest_noop(tmp_path):
    proj = tmp_path / "d_abB"
    (proj / "constraints").mkdir(parents=True)
    config = proj / "constraints" / "config.mk"
    config.write_text("export CORE_UTILIZATION = 20\n")
    before = config.read_text()
    el._apply_recipe_strategy(
        {"project_path": str(proj), "strategy": "pin_perimeter_floor"})
    assert config.read_text() == before


def test_pin_perimeter_recipe_replays_recorded_effect_without_old_error_log(tmp_path):
    """A clean subject no longer has PPL-0024 in its newest backend; B uses fix evidence."""
    proj = tmp_path / "d_abB"
    (proj / "constraints").mkdir(parents=True)
    config = proj / "constraints" / "config.mk"
    config.write_text("export CORE_UTILIZATION = 25\n")
    delta = {
        "CORE_UTILIZATION": {"before": "25", "after": None},
        "DIE_AREA": {"before": None, "after": "0 0 510 510"},
        "CORE_AREA": {"before": None, "after": "10 10 500 500"},
    }
    el._apply_recipe_strategy({
        "project_path": str(proj),
        "strategy": "pin_perimeter_floor",
        "recipe_config_delta": delta,
    })
    cfg = config.read_text()
    assert "CORE_UTILIZATION" not in cfg
    assert "DIE_AREA = 0 0 510 510" in cfg
    assert "CORE_AREA = 10 10 500 500" in cfg


def test_pin_perimeter_fix_has_distinct_learning_identity(tmp_path):
    proj = tmp_path / "p"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 25\n")
    before = el._config_snapshot({"project_path": str(proj)})
    assert el._set_explicit_die(str(proj), 851.76)
    entry = {
        "project_path": str(proj),
        "_r2g_config_effect": el._config_effect(
            before, el._config_snapshot({"project_path": str(proj)})),
    }
    el._record_pin_perimeter_fix(entry, cleared=True)
    row = __import__("json").loads(
        (proj / "reports" / "fix_log.jsonl").read_text().strip())
    assert row["strategy"] == "pin_perimeter_floor"
    assert row["verdict"] == "cleared"
    assert row["effect_fingerprint"]


def test_apply_recipe_strategy_no_target_keeps_util_lever(tmp_path):
    """Without a pin target (FLW-0024 / generic place arm), arm B still lowers util."""
    proj = tmp_path / "d_abB"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 20\n")
    el._apply_recipe_strategy(
        {"project_path": str(proj), "strategy": "core_util_relief"})
    cfg = (proj / "constraints" / "config.mk").read_text()
    m = re.search(r"CORE_UTILIZATION\s*=\s*(\d+)", cfg)
    assert m and int(m.group(1)) < 20 and "DIE_AREA" not in cfg


# ── plan_arms_for_candidates carries the exact recorded effect ───────
def test_plan_arms_stamps_recorded_pin_effect_after_subject_is_clean(tmp_path, monkeypatch):
    import recipe_lifecycle
    import ab_runner
    subj = tmp_path / "subj"
    (subj / "constraints").mkdir(parents=True)
    _seed_ppl(subj)
    _seed_recorded_pin_fix(subj)
    clean_run = subj / "backend" / "RUN_2026-06-28_00-00-00"
    clean_run.mkdir(parents=True)
    (clean_run / "flow.log").write_text("Flow complete without PPL errors\n")
    key = {"symptom_id": "af17c0ba7f62c48e", "design_class": "logic/small",
           "platform": "nangate45", "strategy": "pin_perimeter_floor"}
    monkeypatch.setattr(el, "_ab_coverage_gap", lambda conn, k: False)
    monkeypatch.setattr(el, "_symptom_check", lambda conn, sid, strat: "place")
    monkeypatch.setattr(recipe_lifecycle, "pending_candidates", lambda conn: [key])
    monkeypatch.setattr(ab_runner, "plan_trial",
                        lambda conn, **kw: {"designs": [{"project_path": str(subj)}],
                                            "match_level": "exact"})
    monkeypatch.setattr(ab_runner, "ab_repeats", lambda: 1)
    led = el.Ledger(tmp_path / "l.jsonl")
    el.plan_arms_for_candidates(led, None, n_ab_designs=1)
    arms = [e for e in led.entries() if e.get("kind") == "ab_arm"]
    assert arms, "no arm entries planned"
    assert all("pin_perimeter_target" not in e for e in arms), \
        "test precondition broken: newest clean run should not expose old PPL text"
    assert all(e.get("recipe_config_delta") for e in arms), \
        "place arms missing the provenance-bound exact effect"
    assert len({e["baseline_config_sha"] for e in arms}) == 1
    for arm in arms:
        cfg = (__import__("pathlib").Path(arm["project_path"])
               / "constraints" / "config.mk").read_text()
        assert "CORE_UTILIZATION = 12" in cfg
        assert "DIE_AREA" not in cfg and "CORE_AREA" not in cfg


def test_plan_arms_rejects_pin_subject_without_pre_fix_evidence(tmp_path, monkeypatch):
    """A post-fix PPL subject without structured before/after evidence is not a control."""
    import recipe_lifecycle
    import ab_runner
    subj = tmp_path / "legacy_fixed_subj"
    (subj / "constraints").mkdir(parents=True)
    (subj / "constraints" / "config.mk").write_text(
        "export DIE_AREA = 0 0 230 230\nexport CORE_AREA = 10 10 220 220\n")
    _seed_ppl(subj)
    key = {"symptom_id": "af17c0ba7f62c48e", "design_class": "logic/small",
           "platform": "sky130hd", "strategy": "pin_perimeter_floor"}
    monkeypatch.setattr(el, "_ab_coverage_gap", lambda conn, k: False)
    monkeypatch.setattr(el, "_symptom_check", lambda conn, sid, strat: "place")
    monkeypatch.setattr(recipe_lifecycle, "pending_candidates", lambda conn: [key])
    monkeypatch.setattr(ab_runner, "plan_trial",
                        lambda conn, **kw: {"designs": [{"project_path": str(subj)}],
                                            "match_level": "exact"})
    led = el.Ledger(tmp_path / "l.jsonl")
    assert el.plan_arms_for_candidates(led, None, n_ab_designs=1, repeats=1) == 0
    assert not [e for e in led.entries() if e.get("kind") == "ab_arm"]
