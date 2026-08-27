"""End-to-end checks for the independent Parametric shadow campaign runner."""
from __future__ import annotations

import hashlib
import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_parametric_shadow_campaign import (
    append_outcomes,
    join_and_report,
    prepare,
)
from tehm import db
from tehm.ids import stable_dumps
from tehm.parametric.shadow_campaign import (AppendOnlyShadowLog,
                                              ShadowCampaignError,
                                              action_digest)


def _context() -> dict:
    body = {
        "platform": "sky130hd",
        "dataset_tier": "strict_clean",
        "graph_features": {"num_cells": 12.0, "avg_fanout": 1.2},
        "topology_rows": {"nets": 8},
        "extractor_version": "graph-v0.1",
    }
    body["digest"] = hashlib.sha256(stable_dumps(body).encode()).hexdigest()
    return body


def _policy() -> dict:
    return {
        "family": "DENSITY_RELIEF",
        "scope": {"platform": "sky130hd", "family": "DENSITY_RELIEF",
                  "dataset_tier": "strict_clean"},
        "status": "ready", "version": "cal-v0.1",
        "firewall": {"heldout_lineages": ["heldout:a", "heldout:b"],
                      "disjoint": True, "overlap": []},
        "thresholds": {"max_distance": 3.0, "required_coverage": 0.8},
        "calibration": {"empirical_coverage": 1.0,
                         "required_metrics": ["area_um2"]},
    }


def _readiness() -> dict:
    return {
        "status": "READY_FOR_IMPLEMENTATION",
        "parametric_view_status": "NOT_IMPLEMENTED",
        "criteria": {
            "all_retrieval_policies_ready": True,
            "distance_gate_satisfied": True,
            "coverage_gate_satisfied": True,
            "uncertainty_gate_satisfied": True,
            "lineage_diversity_satisfied": True,
            "minimum_independent_heldout_lineages": 2,
            "observed_independent_heldout_lineages": 2,
        },
    }


def _replay() -> dict:
    return {"ok": True, "roundtrip_byte_stable": True,
            "bundle_digest": "bundle-a", "manifest_digest": "manifest-a"}


def _action() -> dict:
    return {"domain": "flow.CONFIG_DELTA",
            "payload": {"config_edits": {"CORE_UTILIZATION": "22"}},
            "transformation_family": "DENSITY_RELIEF"}


def _manifest(path: Path) -> None:
    cases = []
    for suffix in ("a", "b"):
        lineage = f"future:{suffix}"
        cases.append({
            "case_id": f"{lineage}:observation",
            "target_id": f"{lineage}:target",
            "lineage_id": lineage,
            "platform": "sky130hd", "family": "DENSITY_RELIEF",
            "phase": "observation", "graph_context_digest": _context()["digest"],
            "candidate_actions": [_action()],
        })
    value = {
        "version": "parametric-prospective-manifest-v1", "status": "FROZEN",
        "source_freeze": {"bundle_digest": "bundle-a", "manifest_digest": "manifest-a"},
        "firewall": {"training_lineages": [], "calibration_lineages": [],
                      "heldout_lineages": [], "ab_lineages": []},
        "cases": cases,
        "pre_registered_metrics": {"hard_ood_ceiling": 3.0,
                                    "min_interval_coverage": 0.8,
                                    "max_harmful_rate": 0.1,
                                    "min_obligation_coverage": 0.95},
    }
    path.write_bytes(stable_dumps(value).encode())


def _cases(path: Path) -> None:
    context = _context()
    rows = []
    for suffix in ("a", "b"):
        lineage = f"future:{suffix}"
        rows.append({
            "case_id": f"{lineage}:observation", "source_lineage": lineage,
            "mode": "observation", "family": "DENSITY_RELIEF",
            "graph_context": context, "action": _action(),
            "calibration_policy": _policy(),
            "policy_scope": _policy()["scope"], "candidate_rank": 1,
        })
    path.write_bytes(b"\n".join(stable_dumps(row).encode() for row in rows) + b"\n")


def _ppa(area: float) -> dict:
    return {"summary": {"timing": {"setup_wns": 1.0, "setup_tns": 0.0},
                         "area": {"design_area_um2": area},
                         "power": {"total_power_w": 1.0}}}


def test_campaign_prepare_resume_join_isolated_from_canonical(tmp_path, monkeypatch):
    # The runner must open this snapshot immutable; no WAL/SHM sidecar or row
    # mutation may be produced while receipts are prepared.
    db_path = tmp_path / "snapshot.sqlite"
    conn = db.connect(db_path)
    db.ensure_schema(conn)
    conn.close()
    before = db_path.read_bytes()

    manifest_path = tmp_path / "prospective.json"
    cases_path = tmp_path / "cases.jsonl"
    _manifest(manifest_path)
    _cases(cases_path)
    # Replace the real predictor with a deterministic read-only test double;
    # all campaign integrity checks still run through the production runner.
    class Memory:
        def predict(self, **kwargs):
            return {
                "family": "DENSITY_RELIEF", "abstained": False,
                "abstain_reasons": [], "nearest_distance": 0.5,
                "max_distance": 3.0, "support": 3, "unique_graph_contexts": 3,
                "mean_deltas": {"area_um2": 1.0},
                "uncertainty_95": {"area_um2": {"lower_95": 0.0, "upper_95": 2.0}},
                "gradient_claimed": False,
            }

    monkeypatch.setattr("run_parametric_shadow_campaign.PhysicalEffectMemory", lambda conn: Memory())
    out = tmp_path / "campaign"
    log = out / "events.jsonl"
    args = Namespace(out_dir=out, log=log, db=db_path, cases=cases_path,
                     readiness=_write_json(tmp_path / "readiness.json", _readiness()),
                     replay_evidence=_write_json(tmp_path / "replay.json", _replay()),
                     prospective_manifest=manifest_path, outcomes=None,
                     observation_metrics=None)

    first = prepare(args)
    second = prepare(args)  # resume must not duplicate receipts
    assert first["canonical_memory_unchanged"] is True
    assert second["canonical_memory_unchanged"] is True
    assert len(log.read_bytes().splitlines()) == 2
    assert db_path.read_bytes() == before
    assert not db_path.with_name(db_path.name + "-wal").exists()
    assert not db_path.with_name(db_path.name + "-shm").exists()

    outcomes = tmp_path / "outcomes.jsonl"
    rows = []
    for envelope in AppendOnlyShadowLog(log).read():
        receipt = envelope["event"]
        rows.append({"case_id": receipt["case_id"], "before_ppa": _ppa(10),
                     "after_ppa": _ppa(11),
                     "oracle": {"obligation_coverage": 1.0}})
    outcomes.write_bytes(b"\n".join(stable_dumps(row).encode() for row in rows) + b"\n")
    outcome_args = Namespace(out_dir=out, log=log, outcomes=outcomes)
    append_outcomes(outcome_args)
    report = join_and_report(Namespace(out_dir=out, log=log))
    assert report["join"]["joined_count"] == 2
    assert report["metrics"]["outcome_coverage"] == 1.0
    assert report["metrics"]["proposal_coverage"] == 1.0

    schema = json.loads((ROOT / "evaluation" /
                         "parametric_shadow_receipt_v1.schema.json").read_text())
    assert schema["properties"]["record_version"]["const"] == "parametric-shadow-receipt-v1"
    assert set(schema["required"]) >= {
        "target_graph_context_digest", "action_digest", "provenance"
    }
    assert set(schema["properties"]["provenance"]["required"]) >= {
        "policy_digest", "bundle_digest", "manifest_digest"
    }


def test_campaign_expands_decision_candidates_and_binds_policies(tmp_path, monkeypatch):
    db_path = tmp_path / "snapshot.sqlite"
    conn = db.connect(db_path)
    db.ensure_schema(conn)
    conn.close()
    context = _context()
    action_a = _action()
    action_b = {"domain": action_a["domain"],
                "payload": {"config_edits": {"CORE_UTILIZATION": "40"}},
                "transformation_family": action_a["transformation_family"]}
    policy_a = _policy()
    policy_b = {**_policy(), "action_signature": {
        "domain": action_b["domain"],
        "transformation_family": action_b["transformation_family"],
        "config_edit_keys": ["CORE_UTILIZATION"],
        "config_edit_values": {"CORE_UTILIZATION": "40"},
    }}
    manifest = {
        "version": "parametric-prospective-manifest-v1", "status": "FROZEN",
        "source_freeze": {"bundle_digest": "bundle-a", "manifest_digest": "manifest-a"},
        "firewall": {"training_lineages": [], "calibration_lineages": [],
                      "heldout_lineages": [], "ab_lineages": []},
        "cases": [{
            "case_id": "future:a:decision", "target_id": "future:a:target",
            "lineage_id": "future:a", "platform": "sky130hd",
            "family": "DENSITY_RELIEF", "phase": "decision",
            "graph_context_digest": context["digest"],
            "candidate_actions": [action_a, action_b],
        }, {
            "case_id": "future:b:observation", "target_id": "future:b:target",
            "lineage_id": "future:b", "platform": "sky130hd",
            "family": "DENSITY_RELIEF", "phase": "observation",
            "graph_context_digest": context["digest"],
            "candidate_actions": [action_a],
        }],
        "pre_registered_metrics": {"hard_ood_ceiling": 3.0,
                                    "min_interval_coverage": 0.8,
                                    "max_harmful_rate": 0.1,
                                    "min_obligation_coverage": 0.95},
        "decision_gate": {
            "min_observation_proposal_coverage": 0.0,
            "min_observation_outcome_coverage": 0.0,
            "min_observation_obligation_coverage": 0.0,
            "required_physical_metrics": ["area_um2"],
            "min_metric_evaluations": 1,
        },
    }
    manifest_path = tmp_path / "prospective.json"
    manifest_path.write_bytes(stable_dumps(manifest).encode())
    cases_path = tmp_path / "decision_cases.jsonl"
    cases_path.write_bytes(stable_dumps({
        "case_id": "future:a:decision", "source_lineage": "future:a",
        "mode": "decision", "family": "DENSITY_RELIEF", "graph_context": context,
        "candidate_actions": [action_a, action_b],
        "calibration_policies": {
            action_digest(action_a): policy_a,
            action_digest(action_b): policy_b,
        },
    }).encode() + b"\n")
    observation_metrics = _write_json(tmp_path / "observation_metrics.json", {
        "metrics": {"proposal_coverage": 1.0, "outcome_coverage": 1.0,
                     "obligation_coverage_min": 1.0,
                     "harmful_outcome_rate": 0.0,
                     "ood_distance": {"max": 0.5},
                     "physical_metrics": {"area_um2": {"evaluated": 1,
                                                         "interval_coverage": 1.0}}}
    })

    class Memory:
        def predict(self, **kwargs):
            return {
                "family": "DENSITY_RELIEF", "abstained": False,
                "abstain_reasons": [], "nearest_distance": 0.5,
                "max_distance": 3.0, "support": 3, "unique_graph_contexts": 3,
                "mean_deltas": {"area_um2": 1.0},
                "uncertainty_95": {"area_um2": {"lower_95": 0.0, "upper_95": 2.0}},
                "gradient_claimed": False,
            }

    monkeypatch.setattr("run_parametric_shadow_campaign.PhysicalEffectMemory",
                        lambda conn: Memory())
    out = tmp_path / "campaign"
    args = Namespace(out_dir=out, log=out / "events.jsonl", db=db_path,
                     cases=cases_path,
                     readiness=_write_json(tmp_path / "readiness.json", _readiness()),
                     replay_evidence=_write_json(tmp_path / "replay.json", _replay()),
                     prospective_manifest=manifest_path, outcomes=None,
                     observation_metrics=observation_metrics)
    snapshot = prepare(args)
    assert snapshot["input_case_count"] == 1
    assert snapshot["case_count"] == 2
    receipts = [envelope["event"]
                for envelope in AppendOnlyShadowLog(args.log).read()]
    assert [receipt["candidate_rank"] for receipt in receipts] == [1, 2]
    assert [receipt["action"]["payload"]["config_edits"]["CORE_UTILIZATION"]
            for receipt in receipts] == ["22", "40"]


def test_campaign_accepts_observation_row_with_redundant_single_candidate(tmp_path,
                                                                           monkeypatch):
    db_path = tmp_path / "snapshot.sqlite"
    conn = db.connect(db_path)
    db.ensure_schema(conn)
    conn.close()
    manifest_path = tmp_path / "prospective.json"
    _manifest(manifest_path)
    cases_path = tmp_path / "cases.jsonl"
    context = _context()
    action = _action()
    cases_path.write_bytes(stable_dumps({
        "case_id": "future:a:observation", "source_lineage": "future:a",
        "mode": "observation", "family": "DENSITY_RELIEF",
        "graph_context": context, "action": action,
        "candidate_actions": [action], "calibration_policy": _policy(),
        "policy_scope": _policy()["scope"],
    }).encode() + b"\n")

    class Memory:
        def predict(self, **kwargs):
            return {"family": "DENSITY_RELIEF", "abstained": False,
                    "abstain_reasons": [], "nearest_distance": 0.5,
                    "max_distance": 3.0, "support": 3,
                    "unique_graph_contexts": 3, "mean_deltas": {"area_um2": 1.0},
                    "uncertainty_95": {"area_um2": {"lower_95": 0.0,
                                                       "upper_95": 2.0}},
                    "gradient_claimed": False}
    monkeypatch.setattr("run_parametric_shadow_campaign.PhysicalEffectMemory",
                        lambda conn: Memory())
    out = tmp_path / "campaign"
    args = Namespace(out_dir=out, log=out / "events.jsonl", db=db_path,
                     cases=cases_path,
                     readiness=_write_json(tmp_path / "readiness.json", _readiness()),
                     replay_evidence=_write_json(tmp_path / "replay.json", _replay()),
                     prospective_manifest=manifest_path, outcomes=None,
                     observation_metrics=None)
    snapshot = prepare(args)
    assert snapshot["case_count"] == 1


def test_campaign_rejects_memory_snapshot_digest_mismatch(tmp_path):
    db_path = tmp_path / "snapshot.sqlite"
    conn = db.connect(db_path)
    db.ensure_schema(conn)
    conn.close()
    manifest_path = tmp_path / "prospective.json"
    _manifest(manifest_path)
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_bytes(stable_dumps({
        "case_id": "future:a:observation", "source_lineage": "future:a",
        "mode": "observation", "family": "DENSITY_RELIEF",
        "graph_context": _context(), "action": _action(),
        "calibration_policy": _policy(),
        "memory_snapshot_digest": "wrong-snapshot",
    }).encode() + b"\n")
    args = Namespace(
        out_dir=tmp_path / "campaign", log=tmp_path / "campaign/events.jsonl",
        db=db_path, cases=cases_path,
        readiness=_write_json(tmp_path / "readiness.json", _readiness()),
        replay_evidence=_write_json(tmp_path / "replay.json", _replay()),
        prospective_manifest=manifest_path, outcomes=None,
        observation_metrics=None,
    )
    with pytest.raises(ShadowCampaignError, match="memory snapshot digest mismatch"):
        prepare(args)


def _write_json(path: Path, value: dict) -> Path:
    path.write_bytes(stable_dumps(value).encode())
    return path
