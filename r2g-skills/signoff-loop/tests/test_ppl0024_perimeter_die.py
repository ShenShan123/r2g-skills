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
        {"project_path": str(proj), "strategy": "core_util_relief",
         "pin_perimeter_target": 851.76})
    cfg = (proj / "constraints" / "config.mk").read_text()
    assert "DIE_AREA" in cfg and "CORE_UTILIZATION" not in cfg
    assert _core_perimeter(cfg) >= 851.76


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


# ── plan_arms_for_candidates stamps the SUBJECT's perimeter onto place arms ──
def test_plan_arms_stamps_pin_target_for_place(tmp_path, monkeypatch):
    import recipe_lifecycle
    import ab_runner
    subj = tmp_path / "subj"
    (subj / "constraints").mkdir(parents=True)
    (subj / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 12\n")
    _seed_ppl(subj)
    key = {"symptom_id": "af17c0ba7f62c48e", "design_class": "logic/small",
           "platform": "nangate45", "strategy": "core_util_relief"}
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
    assert all(e.get("pin_perimeter_target") == 851.76 for e in arms), \
        "place arms missing the subject's PPL-0024 perimeter target"
