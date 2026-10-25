"""TEHM policy policy wiring in diagnose_signoff_fix (TEHM arm only; frozen policy + index)."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

import diagnose_signoff_fix as D

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "memory"))
from tehm.policy import state as S  # noqa: E402

MECH = {"typed_matching": True, "applicability": True, "evidence_ranking": True, "repeat_control": True}


def _policy(tmp_path, entries, mechanisms=MECH, sha=None):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"schema": "tehm-v2-evidence-index-v1", "entries": entries}))
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps({"schema": "tehm-v2-policy-v1", "mechanisms": mechanisms, "index": str(idx),
                               "index_sha256": sha or hashlib.sha256(idx.read_bytes()).hexdigest(),
                               "task_ip_family": "secworks/chacha"}))
    return pol


def _proj(tmp_path, rows=()):
    p = tmp_path / "proj"
    (p / "reports").mkdir(parents=True)
    (p / "reports" / "fix_log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_the_policy_rejects_a_drv_rule_on_setup_and_records_every_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_TEHM_POLICY", str(_policy(tmp_path, [])))
    plan = {"strategies": [{"id": "tehm_drv", "source": "tehm_rule", "config_edits": {"R2G_ROUTE_SLEW_MARGIN": "10"}},
                           {"id": "period_relax", "source": "catalogue", "config_edits": {"CORE_UTILIZATION": "30"}}]}
    D._apply_tehm_policy(plan, _proj(tmp_path), "timing", {"CORE_UTILIZATION": "40"}, {}, {},
                     {"tier": "severe", "wns_ns": -0.4, "drv": {"status": "pass"}}, None)
    assert [s["id"] for s in plan["strategies"]] == ["period_relax"]
    assert plan["tehm_policy"]["state"]["failures"] == ["setup"]
    assert {d["id"]: d["decision"] for d in plan["tehm_policy"]["decisions"]} == {"tehm_drv": "rejected",
                                                                             "period_relax": "kept"}


def test_an_index_hash_mismatch_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_TEHM_POLICY", str(_policy(tmp_path, [], sha="0" * 64)))
    with pytest.raises(ValueError, match="hash mismatch"):
        D._apply_tehm_policy({"strategies": []}, _proj(tmp_path), "drc", {}, {}, {}, {}, None)


def test_the_episode_log_feeds_repeat_control(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_TEHM_POLICY", str(_policy(tmp_path, [])))
    row = {"strategy": "density_relief", "verdict": "no_improvement", "config_before": {"CORE_UTILIZATION": "40"},
           "config_delta": json.dumps({"CORE_UTILIZATION": "30"}), "rolled_back": False}
    plan = {"strategies": [{"id": "density_relief", "source": "catalogue", "config_edits": {"CORE_UTILIZATION": "30"}}]}
    D._apply_tehm_policy(plan, _proj(tmp_path, [row]), "drc", {"CORE_UTILIZATION": "40"},
                     {"categories": {"li.3": {"count": 4}}}, {}, {}, None)
    assert plan["strategies"] == [] and plan["tehm_policy"]["decisions"][0]["mechanism"] == "repeat_control"


@pytest.mark.parametrize("timing,failures", [
    ({"tier": "severe", "wns_ns": -0.4, "drv": {"status": "fail"}}, ["setup", "drv"]),
    ({"tier": "clean", "wns_ns": 0.2, "drv": {"status": "fail"}}, ["drv"]),
    ({"tier": "unconstrained", "wns_ns": 1e39, "drv": {"status": "pass"}}, []),
    ({"tier": "severe", "wns_ns": -0.4, "hold_wns": -0.1}, ["setup", "hold"])])
def test_typed_timing_state(timing, failures):
    assert S.from_reports("timing", None, None, None, timing, {})["failures"] == failures


def test_route_and_drc_states():
    assert S.from_reports("route", {"violation_class": "route", "error_code": "GRT-0116"}, None, None, None, {}
                          )["signature"] == "route:GRT-0116"
    assert S.from_reports("drc", None, {"categories": {"li.3": {"count": 4}, "m3.2": 1}}, None, None, {}
                          )["signature"] == "drc:li.3"
