"""T2a A2 acceptance tests (contract 3.3 / 5.2): the per-task constraint and area envelope rejects out-of-range
effective overrides and clean-signoff outputs beyond frozen core/die bounds, and refuses acceptance without the
mandatory area evidence."""
import hashlib
import json
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "memory" / "scripts"))
from tehm.policy import envelope as E  # noqa: E402

MAN = {"task": "ibex/u80",
       "initial_config": {"CORE_UTILIZATION": "80", "PLACE_DENSITY_LB_ADDON": "0.2", "DESIGN_NAME": "ibex_core"},
       "immutable": {"knobs": ["DESIGN_NAME", "PLATFORM"], "files": {}},
       "allowlist": {"CORE_UTILIZATION": {"type": "number", "min": 30, "max": 90, "max_step": 30},
                     "PLACE_DENSITY_LB_ADDON": {"type": "number", "min": 0.10, "max": 0.40},
                     "GPL_ROUTABILITY_DRIVEN": {"type": "enum", "values": ["0", "1"]}},
       "area": {"stdcell": {"ref": 100000, "ref_stage": "synth", "max_rel_increase": 0.15},
                "core": {"ref": 200000, "ref_stage": "floorplan", "max_rel_increase": 0.40},
                "die": {"ref": 210000, "ref_stage": "floorplan", "max_rel_increase": 0.40}}}


def _proj(tmp_path, cfg, report=None, last_stage=("finish", 0)):
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    (p / "constraints" / "config.mk").write_text("".join("export %s = %s\n" % kv for kv in cfg.items()))
    run = p / "backend" / "RUN_x"
    (run / "logs").mkdir(parents=True)
    order = ["floorplan", "place", "cts", "route", "finish"]
    stages = [(s, 0) for s in order[:order.index(last_stage[0])]] + [last_stage]
    (run / "stage_log.jsonl").write_text("".join(json.dumps({"stage": s, "status": st}) + "\n" for s, st in stages))
    if report is not None:
        (run / "logs" / "6_report.json").write_text(json.dumps(report))
    return p, run


def _report(stdcell, core, die):
    return {E.AREA_KEYS["stdcell"]: stdcell, E.AREA_KEYS["core"]: core, E.AREA_KEYS["die"]: die}


OK_CFG = {**MAN["initial_config"], "CORE_UTILIZATION": "60", "GPL_ROUTABILITY_DRIVEN": "1"}


def test_an_in_envelope_repair_passes(tmp_path):
    p, run = _proj(tmp_path, OK_CFG, _report(105000, 260000, 270000))
    r = E.envelope(MAN, p, run)
    assert r["status"] == "PASS" and abs(r["area"]["metrics"]["core"]["relative_change"] - 0.30) < 1e-9


def test_out_of_range_or_unlisted_effective_overrides_are_rejected(tmp_path):
    for cfg, kind in (({**OK_CFG, "CORE_UTILIZATION": "20"}, "out_of_range"),
                      ({**OK_CFG, "CORE_UTILIZATION": "45"}, "step_too_large"),
                      ({**OK_CFG, "MAX_ROUTING_LAYER": "met5"}, "not_allowlisted"),
                      ({**OK_CFG, "GPL_ROUTABILITY_DRIVEN": "2"}, "value_not_allowed"),
                      ({**OK_CFG, "DESIGN_NAME": "other"}, "immutable_knob_changed")):
        p, run = _proj(tmp_path / kind, cfg, _report(105000, 260000, 270000))
        r = E.envelope(MAN, p, run)
        assert r["status"] == "FAIL" and kind in {v["kind"] for v in r["config"]["violations"]}, kind
    p, run = _proj(tmp_path / "env", OK_CFG, _report(105000, 260000, 270000))
    r = E.envelope(MAN, p, run, env={"CORE_UTILIZATION": "40"})          # an equivalent override outside config.mk
    assert r["status"] == "FAIL" and r["config"]["violations"][0]["kind"] == "environment_override"


def test_a_clean_signoff_output_beyond_the_core_or_die_bound_is_rejected(tmp_path):
    p, run = _proj(tmp_path, OK_CFG, _report(105000, 300000, 290000))   # core +50 % > 40 %
    r = E.envelope(MAN, p, run)
    assert r["status"] == "FAIL" and r["area"]["violations"][0]["metric"] == "core"
    p, run = _proj(tmp_path / "d", OK_CFG, _report(105000, 270000, 310000))   # die +47.6 %
    assert E.envelope(MAN, p, run)["area"]["violations"][0]["metric"] == "die"


def test_missing_mandatory_area_evidence_refuses_acceptance(tmp_path):
    p, run = _proj(tmp_path, OK_CFG, report=None)                        # no finish report at all
    r = E.envelope(MAN, p, run)
    assert r["status"] == "UNVERIFIED" and set(r["area"]["unverified"]) == {"stdcell", "core", "die"}
    p, run = _proj(tmp_path / "z", OK_CFG, _report(105000, 260000, 270000))
    man = {**MAN, "area": {**MAN["area"], "core": {**MAN["area"]["core"], "ref": 0}}}   # zero reference: UNKNOWN
    assert E.envelope(man, p, run)["status"] == "UNVERIFIED"


def test_an_explicitly_unconstrained_metric_is_measured_but_not_limited(tmp_path):
    man = {**MAN, "area": {**MAN["area"], "stdcell": {**MAN["area"]["stdcell"], "max_rel_increase": None}}}
    p, run = _proj(tmp_path, OK_CFG, _report(500000, 260000, 270000))
    r = E.envelope(man, p, run)
    assert r["status"] == "PASS" and r["area"]["metrics"]["stdcell"]["relative_change"] > 1


def test_missing_area_schema_cannot_pass():
    assert not E.check_area({}, {})['ok']
    man = {**MAN, 'area': {'stdcell': MAN['area']['stdcell']}}
    assert not E.check_area(man, {'stdcell': 100000})['ok']


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, 0, True])
def test_invalid_area_or_reference_cannot_pass(bad):
    metrics = {'stdcell': 105000, 'core': 260000, 'die': 270000}
    assert not E.check_area(MAN, {**metrics, 'core': bad})['ok']
    man = {**MAN, 'area': {**MAN['area'], 'core': {**MAN['area']['core'], 'ref': bad}}}
    assert not E.check_area(man, metrics)['ok']


def test_core_and_die_must_have_finite_caps():
    for cap in (None, float('nan'), float('inf'), 'invalid'):
        man = {**MAN, 'area': {**MAN['area'], 'core': {**MAN['area']['core'], 'max_rel_increase': cap}}}
        assert not E.check_area(man, {'stdcell': 105000, 'core': 260000, 'die': 270000})['ok']


def test_plain_later_assignment_is_not_ignored():
    assert E.effective_config('export CORE_UTILIZATION = 80\nCORE_UTILIZATION = 20\n')['CORE_UTILIZATION'] == '20'


@pytest.mark.parametrize('line', ['include other.mk', 'export CORE_UTILIZATION = $(X)',
                                'override CORE_UTILIZATION = 20'])
def test_unresolved_make_syntax_fails_closed(line):
    with pytest.raises(ValueError):
        E.effective_config(line)


def test_immutable_files_are_checked_by_hash(tmp_path):
    p, run = _proj(tmp_path, OK_CFG, _report(105000, 260000, 270000))
    (p / "constraints" / "constraint.sdc").write_text("set clk_period 10\n")
    digest = "sha256:" + hashlib.sha256(b"set clk_period 10\n").hexdigest()
    man = {**MAN, "immutable": {**MAN["immutable"], "files": {"constraints/constraint.sdc": digest}}}
    assert E.envelope(man, p, run)["status"] == "PASS"
    (p / "constraints" / "constraint.sdc").write_text("set clk_period 20\n")   # a relaxed clock
    assert E.envelope(man, p, run)["config"]["violations"][0]["kind"] == "immutable_file_changed"


def test_intermediate_violations_are_kept_even_when_rolled_back():
    rows = [{"iter": 1, "strategy": "x", "config_delta": {"CORE_UTILIZATION": {"before": "80", "after": "20"}},
             "rolled_back": True},
            {"iter": 2, "strategy": "y", "config_delta": {"CORE_UTILIZATION": "60"}, "rolled_back": False}]
    v = E.recorded_violations(MAN, rows)
    assert sorted((x["iter"], x["kind"], x["rolled_back"]) for x in v) == [(1, "out_of_range", True),
                                                                          (1, "step_too_large", True)]


def test_a_finish_report_the_run_did_not_produce_is_never_measured(tmp_path):
    """failure-patterns #61: a rerun that aborted at route carried the previous flow's
    6_report.json; its areas must read UNKNOWN (never a PASS on someone else's layout)."""
    p, run = _proj(tmp_path, {"CORE_UTILIZATION": "40"}, _report(100, 100, 100), last_stage=("route", 2))
    assert E.area_metrics(run) == {"stdcell": None, "core": None, "die": None}
    p2, run2 = _proj(tmp_path / "b", {"CORE_UTILIZATION": "40"}, _report(100, 100, 100))
    assert E.area_metrics(run2) == {"stdcell": 100, "core": 100, "die": 100}
