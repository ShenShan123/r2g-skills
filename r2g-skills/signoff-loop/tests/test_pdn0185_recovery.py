"""PDN-0185 (floorplan pdngen: 'Insufficient width to add straps') is a RECOVERABLE
'die too NARROW for the power straps' abort, not an unseen crash. The loop must floor
the die to an explicit PDN-feasible size (dropping CORE_UTILIZATION) and retry; only if
it STILL cannot lay straps does it escalate, with the honest reason `pdn_strap_residual`.

Root cause (2026-07-01 sky130 round): a tiny design auto-sizes (CORE_UTILIZATION) a die
only ~27um wide, but a sky130hd met4/met5 strap set needs ~28.8um, so pdngen aborts
REGARDLESS of utilization (tools/mk_sky130_project.py:199 — new projects get a 200um
PDN_DIE_FLOOR at setup, but the corpus re-point via setup_rtl_designs.py does not). Three
tiny designs (8-bit control logic, AMBA apb_protocol) were mislabeled `unseen_crash`
because the loop had no PDN-strap handler. Distinct from FLW-0024 (die too small for the
CELLS) and PPL-0024 (die perimeter too short for the PINS).
"""
import json

import engineer_loop as el

_PDN_MSG = ("[ERROR PDN-0185] Insufficient width ({w} um) to add straps on layer met5 "
            'in grid "grid" with total strap width 15.2 um and offset 13.6 um.\n')


def _mk_project(tmp_path, *, core_util=True):
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    cfg = p / "constraints" / "config.mk"
    if core_util:
        # the tiny-design case: auto-sized die too narrow for straps REGARDLESS of util
        cfg.write_text("export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\n"
                       "export CORE_UTILIZATION = 25\n"
                       "export PLACE_DENSITY_LB_ADDON = 0.2\n")
    else:
        # an already-wide explicit die (used for the 'do not shrink' no-op test)
        cfg.write_text("export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\n"
                       "export DIE_AREA  = 0 0 400 400\nexport CORE_AREA = 10 10 390 390\n"
                       "export PLACE_DENSITY_LB_ADDON = 0.2\n")
    return p


def _seed_backend(p, *, pdn0185=True, width=27.20, stage="floorplan"):
    run = p / "backend" / "RUN_2026-07-01_00-00-00"
    run.mkdir(parents=True)
    (run / "stage_log.jsonl").write_text(
        '{"stage":"synth","status":0}\n'
        f'{{"stage":"{stage}","status":2}}\n')
    (run / "flow.log").write_text(
        _PDN_MSG.format(w=width) if pdn0185
        else "[ERROR PDN-0179] Unable to repair all channels\n")


# ── _is_pdn_strap_width (distinguishes the strap-fit abort) ───────────────────
def test_is_pdn_strap_width_true_on_pdn0185(tmp_path):
    p = _mk_project(tmp_path)
    _seed_backend(p, pdn0185=True)
    assert el._is_pdn_strap_width({"project_path": str(p)}) is True


def test_is_pdn_strap_width_false_on_other_pdn_error(tmp_path):
    p = _mk_project(tmp_path)
    _seed_backend(p, pdn0185=False)              # PDN-0179 != PDN-0185
    assert el._is_pdn_strap_width({"project_path": str(p)}) is False


# ── _pdn0185_insufficient_width (parse W so we never SHRINK a wide die) ────────
def test_pdn0185_width_parsed(tmp_path):
    p = _mk_project(tmp_path)
    _seed_backend(p, pdn0185=True, width=27.20)
    assert el._pdn0185_insufficient_width(str(p)) == 27.20


def test_pdn0185_width_none_when_absent(tmp_path):
    p = _mk_project(tmp_path)
    _seed_backend(p, pdn0185=False)
    assert el._pdn0185_insufficient_width(str(p)) is None


# ── _relieve_pdn_strap_width (floor the die; never shrink a wide one) ──────────
def test_relieve_floors_die_and_drops_core_util(tmp_path):
    p = _mk_project(tmp_path, core_util=True)
    _seed_backend(p, pdn0185=True, width=27.20)
    assert el._relieve_pdn_strap_width({"project_path": str(p)}) is True
    txt = (p / "constraints" / "config.mk").read_text()
    assert f"DIE_AREA = 0 0 {el._PDN_DIE_FLOOR_UM} {el._PDN_DIE_FLOOR_UM}" in txt
    assert "CORE_UTILIZATION" not in txt          # the CAUSE is removed, not reused
    assert "CORE_AREA = 10 10 190 190" in txt      # inset ring
    assert "PLACE_DENSITY_LB_ADDON = 0.2" in txt   # hard-rule floor untouched
    assert "DESIGN_NAME = demo" in txt


def test_relieve_is_noop_when_die_already_wide(tmp_path):
    # PDN-0185 reported a width >= the floor: the die is NOT small-core; flooring would
    # SHRINK it -> refuse, let it escalate as an honest residual.
    p = _mk_project(tmp_path, core_util=False)
    _seed_backend(p, pdn0185=True, width=400.0)
    assert el._relieve_pdn_strap_width({"project_path": str(p)}) is False
    assert "DIE_AREA  = 0 0 400 400" in (p / "constraints" / "config.mk").read_text()


def test_relieve_is_noop_when_no_config(tmp_path):
    assert el._relieve_pdn_strap_width({"project_path": str(tmp_path / "absent")}) is False


# ── process_one: floor + retry recovers; honest residual if it can't ──────────
class _Led:
    def __init__(self): self.states = []
    def set_state(self, design, state, **k): self.states.append((state, k.get("reason")))


def test_process_one_floors_die_and_retries_then_recovers(tmp_path, monkeypatch):
    p = _mk_project(tmp_path, core_util=True)
    _seed_backend(p, pdn0185=True, width=27.20)
    entry = {"design": "demo", "project_path": str(p), "platform": "sky130hd"}

    flow_calls = {"n": 0}
    def fake_flow(e):
        flow_calls["n"] += 1
        return 2 if flow_calls["n"] == 1 else 0   # PDN-0185 first, clean after die floor
    monkeypatch.setattr(el, "_run_flow", fake_flow)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "floorplan")
    monkeypatch.setattr(el, "_signoff_status", lambda e: {"drc": "clean", "lvs": "clean"})
    cleaned = {}
    monkeypatch.setattr(el, "_mark_clean",
                        lambda led, conn, d, note: cleaned.setdefault(d, note))

    el.process_one(_Led(), entry, None)
    assert flow_calls["n"] == 2                                   # retried once
    txt = (p / "constraints" / "config.mk").read_text()
    assert "DIE_AREA = 0 0 200 200" in txt and "CORE_UTILIZATION" not in txt
    assert "demo" in cleaned                                      # recovered -> clean
    # the die floor must leave a LEARNABLE fix_log row under a DISTINCT floorplan class
    # (not the FLW-0024 place symptom) so the next ingest projects a pdn_die_floor recipe.
    rows = [json.loads(l) for l in
            (p / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    pdn = [r for r in rows if r.get("strategy") == "pdn_die_floor"]
    assert pdn, "PDN recovery left no fix_log row (invisible to learning)"
    assert pdn[0]["check"] == "orfs_stage"
    assert pdn[0]["violation_class"] == "floorplan"              # distinct from 'place'
    assert pdn[0]["verdict"] == "cleared"                         # honest: it cleared


def test_pdn_floor_that_does_not_recover_records_no_change(tmp_path, monkeypatch):
    """A die floor whose retry STILL PDN-fails must record verdict=no_change (negative
    learning) AND escalate the honest reason pdn_strap_residual, never `unseen_crash`."""
    p = _mk_project(tmp_path, core_util=True)
    _seed_backend(p, pdn0185=True, width=27.20)
    entry = {"design": "demo", "project_path": str(p), "platform": "sky130hd"}
    monkeypatch.setattr(el, "_run_flow", lambda e: 2)            # never recovers
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "floorplan")
    led = _Led()
    el.process_one(led, entry, None)
    assert [r for (s, r) in led.states if s == "escalated"] == ["pdn_strap_residual"]
    rows = [json.loads(l) for l in
            (p / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    pdn = [r for r in rows if r.get("strategy") == "pdn_die_floor"]
    assert pdn and pdn[0]["verdict"] == "no_change"             # honest negative


def test_process_one_escalates_pdn_residual_not_unseen_crash(tmp_path, monkeypatch):
    # a die already >= the floor that STILL PDN-fails: the floor is a no-op (would shrink),
    # so it escalates honestly as pdn_strap_residual -- NEVER `unseen_crash`.
    p = _mk_project(tmp_path, core_util=False)
    _seed_backend(p, pdn0185=True, width=400.0)
    entry = {"design": "demo", "project_path": str(p), "platform": "sky130hd"}
    monkeypatch.setattr(el, "_run_flow", lambda e: 2)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "floorplan")
    led = _Led()
    el.process_one(led, entry, None)
    assert [r for (s, r) in led.states if s == "escalated"] == ["pdn_strap_residual"]
