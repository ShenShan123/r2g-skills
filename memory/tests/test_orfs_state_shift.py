"""Pre-execution StateShift context registration and replay."""
from __future__ import annotations

import copy
import json

import pytest

from test_orfs_toolchain_manifest import _fake_orfs
from tehm.adapters.orfs_state_shift import (
    build_flow_state_shift_context, register_flow_state_shift_context,
    recheck_flow_state_shift_context,
)
from tehm.adapters.orfs_terminal_failure import FLOW_CONTRACT, _digest
from tehm.knowledge import MechanismKnowledge
from tehm.orfs_toolchain import build_toolchain_manifest
from tehm.orfs_toolchain_preflight import preflight_orfs_toolchain
from tehm.state import build_support_envelope, evaluate_state_shift


def _knowledge() -> MechanismKnowledge:
    measurement = {"scope": "flow_feasibility", "oracle_type": "TARGET_TEST",
                   "contract_version": FLOW_CONTRACT["version"],
                   "contract_digest": _digest(FLOW_CONTRACT)}
    return MechanismKnowledge(
        knowledge_id="mk-flow-shift", version=1, mechanism_family="DENSITY_RELIEF",
        compatibility_profile=None, antecedent={"source": "density"},
        intervention={"action_domains": ["flow.CONFIG_DELTA"],
                      "measurement_contract": measurement},
        mediated_effects=(), expected_outcome={"oracle_scope": "flow_feasibility"},
        positive_applicability=({"mechanism_family": "DENSITY_RELIEF"},),
        negative_applicability=(),
        preserved_obligations=("oracle_contract:" + measurement["contract_digest"],),
        known_failure_modes=(), causal_path_ids=("path-flow-shift",),
        evidence_level="L3_REPLICATED_EFFECT", support_lineages=("a", "b"),
        status="validated")


def _project(root, utilization="85"):
    project = root / "challenge"
    (project / "constraints").mkdir(parents=True)
    rtl = project / "design.v"
    sdc = project / "constraints/constraint.sdc"
    rtl.write_text("module design(input clk); endmodule\n")
    sdc.write_text("create_clock -period 2.2 [get_ports clk]\n")
    (project / "constraints/config.mk").write_text(
        "export DESIGN_NAME = design\nexport PLATFORM = sky130hs\n"
        f"export VERILOG_FILES = {rtl}\nexport SDC_FILE = {sdc}\n"
        f"export CORE_UTILIZATION = {utilization}\n")
    return project


def _challenge_manifest(root, project, knowledge, envelope, lock_path, lock_digest,
                        *, case_id, lineage_id, name="challenge-manifest.json"):
    path = root / name
    path.write_text(json.dumps({
        "version": "tehm-state-shift-challenge-selection-v1",
        "role": "prospective_challenge_selection",
        "knowledge_parent": knowledge.object_id,
        "support_envelope_digest": envelope.envelope_digest,
        "toolchain_manifest": str(lock_path),
        "toolchain_manifest_digest": lock_digest,
        "reason_label": None,
        "detector_status": "pending",
        "execution_started": False,
        "planned_policies": ["NO_MEMORY", "ALWAYS_MEMORY", "CAUSAL_NO_SKILL"],
        "cases": [
            {"case_id": case_id, "lineage_id": lineage_id,
             "current_project": str(project)},
            {"case_id": case_id + "-control", "lineage_id": lineage_id + "-control",
             "current_project": str(project)},
        ],
    }))
    return path


def test_state_shift_context_is_registered_before_execution_and_replays(tmp_path):
    orfs, _, _ = _fake_orfs(tmp_path / "orfs")
    current = preflight_orfs_toolchain({"orfs_root": str(orfs)}, env={})
    lock = build_toolchain_manifest(current)
    lock_path = tmp_path / "toolchain.json"
    lock_path.write_text(json.dumps(lock))
    knowledge = _knowledge()
    project = _project(tmp_path)
    context = build_flow_state_shift_context(
        project, knowledge, expected_toolchain_manifest_digest=lock["manifest_digest"])
    supported = copy.deepcopy(context)
    supported["constraint_regime"]["core_utilization"] = "95"
    envelope = build_support_envelope(knowledge, (), ({
        "transition_id": "training-pass", "split": "training", "learner_eligible": True,
        "verdict": "PASS", "oracle_complete": True,
        **{key: supported[key] for key in (
            "mechanism_family", "compatibility_profile", "structural_signature",
            "flow_regime", "constraint_regime", "oracle_regime")},
    },))
    challenge = _challenge_manifest(
        tmp_path, project, knowledge, envelope, lock_path, lock["manifest_digest"],
        case_id="challenge-1", lineage_id="shift-1")
    registration = register_flow_state_shift_context(
        project, knowledge, envelope, challenge_id="challenge-1", lineage_id="shift-1",
        challenge_manifest=challenge,
        toolchain_manifest=lock_path,
        expected_toolchain_manifest_digest=lock["manifest_digest"])
    assert registration["reason_label"] is None
    assert registration["detector_result"] == "pending"
    assert registration["execution_started"] is False
    assert registration["current_context"] == context
    assert recheck_flow_state_shift_context(
        project, knowledge, envelope, registration,
        challenge_id="challenge-1", lineage_id="shift-1",
        challenge_manifest=challenge, toolchain_manifest=lock_path,
        expected_toolchain_manifest_digest=lock["manifest_digest"])
    shift = evaluate_state_shift(
        context, {"resolution_id": "prospective-resolution"}, knowledge, envelope)
    assert shift.reason == "STATE_SHIFT" and not shift.transferable
    assert shift.shifted_dimensions == ("constraint_shift",)
    assert shift.current_context_digest == registration["current_context_digest"]
    # A later run does not invalidate a genuinely prior registration.
    (project / "backend/RUN_later").mkdir(parents=True)
    assert recheck_flow_state_shift_context(
        project, knowledge, envelope, registration,
        challenge_id="challenge-1", lineage_id="shift-1",
        challenge_manifest=challenge, toolchain_manifest=lock_path,
        expected_toolchain_manifest_digest=lock["manifest_digest"])


def test_state_shift_registration_rejects_tamper_and_historical_backfill(tmp_path):
    orfs, _, _ = _fake_orfs(tmp_path / "orfs")
    current = preflight_orfs_toolchain({"orfs_root": str(orfs)}, env={})
    lock = build_toolchain_manifest(current)
    lock_path = tmp_path / "toolchain.json"
    lock_path.write_text(json.dumps(lock))
    knowledge = _knowledge()
    project = _project(tmp_path, "95")
    context = build_flow_state_shift_context(
        project, knowledge, expected_toolchain_manifest_digest=lock["manifest_digest"])
    envelope = build_support_envelope(knowledge, (), ({
        "transition_id": "training-pass", "split": "training", "learner_eligible": True,
        "verdict": "PASS", "oracle_complete": True,
        **context,
    },))
    challenge = _challenge_manifest(
        tmp_path, project, knowledge, envelope, lock_path, lock["manifest_digest"],
        case_id="challenge-2", lineage_id="shift-2")
    registration = register_flow_state_shift_context(
        project, knowledge, envelope, challenge_id="challenge-2", lineage_id="shift-2",
        challenge_manifest=challenge,
        toolchain_manifest=lock_path,
        expected_toolchain_manifest_digest=lock["manifest_digest"])
    changed = copy.deepcopy(registration)
    changed["reason_label"] = "STATE_SHIFT"
    assert not recheck_flow_state_shift_context(
        project, knowledge, envelope, changed,
        challenge_id="challenge-2", lineage_id="shift-2",
        challenge_manifest=challenge, toolchain_manifest=lock_path,
        expected_toolchain_manifest_digest=lock["manifest_digest"])
    assert not recheck_flow_state_shift_context(
        project, knowledge, envelope, registration,
        challenge_id="wrong-case", lineage_id="shift-2",
        challenge_manifest=challenge, toolchain_manifest=lock_path,
        expected_toolchain_manifest_digest=lock["manifest_digest"])
    original_manifest = challenge.read_text()
    labelled = json.loads(original_manifest)
    labelled["reason_label"] = "STATE_SHIFT"
    challenge.write_text(json.dumps(labelled))
    assert not recheck_flow_state_shift_context(
        project, knowledge, envelope, registration,
        challenge_id="challenge-2", lineage_id="shift-2",
        challenge_manifest=challenge, toolchain_manifest=lock_path,
        expected_toolchain_manifest_digest=lock["manifest_digest"])
    challenge.write_text(original_manifest)
    (project / "design.v").write_text("module changed; endmodule\n")
    assert not recheck_flow_state_shift_context(
        project, knowledge, envelope, registration,
        challenge_id="challenge-2", lineage_id="shift-2",
        challenge_manifest=challenge, toolchain_manifest=lock_path,
        expected_toolchain_manifest_digest=lock["manifest_digest"])

    historical = tmp_path / "historical"
    project.rename(historical)
    # Use another fresh project to test that a pre-existing run cannot be
    # given a registration after the fact.
    project = _project(tmp_path / "other")
    historical_challenge = _challenge_manifest(
        tmp_path, project, knowledge, envelope, lock_path, lock["manifest_digest"],
        case_id="backfill", lineage_id="backfill", name="historical-manifest.json")
    (project / "backend/RUN_old").mkdir(parents=True)
    with pytest.raises(ValueError, match="precede execution"):
        register_flow_state_shift_context(
            project, knowledge, envelope, challenge_id="backfill", lineage_id="backfill",
            challenge_manifest=historical_challenge,
            toolchain_manifest=lock_path,
            expected_toolchain_manifest_digest=lock["manifest_digest"])
