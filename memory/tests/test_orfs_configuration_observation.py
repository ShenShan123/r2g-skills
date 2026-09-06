"""Configuration-observed execution with a deterministic fake flow (not EDA)."""
from dataclasses import replace
from pathlib import Path
import shutil
import sys

import pytest

from tehm.assets.flow_config import bind_flow_config
from tehm.assets.flow_config_probe import probe_flow_config
from tehm.evaluation.orfs_candidate_oracle import execute_orfs_candidate, OrfsCandidateOracleError
from test_orfs_candidate_oracle import _fake_case, _candidate


def _observed_case(tmp_path):
    case = _fake_case(tmp_path)
    make = shutil.which("make")
    if not make:
        pytest.skip("Make unavailable")
    case.update(make_exe=make, python_exe=sys.executable)
    scripts = Path(case["orfs_root"]) / "flow/scripts"
    scripts.mkdir()
    (scripts / "defaults.py").write_text("# fixture\n")
    (scripts / "variables.json").write_text("{}\n")
    (scripts.parent / "Makefile").write_text(
        f"include $(DESIGN_CONFIG)\nSCRIPTS_DIR := {scripts}\n")
    observation = probe_flow_config(
        Path(case["project_dir"]), Path(case["orfs_root"]), keys=("CORE_UTILIZATION",),
        **{key: Path(case[key]) for key in ("make_exe", "python_exe", "openroad_exe", "yosys_exe")})
    case["flow_config_observation"] = observation
    candidate = _candidate()
    proof = bind_flow_config({"asset_id": candidate.asset_id,
        "definition": {"action": candidate.concrete_action}}, candidate.knowledge_object_id,
        {"flow_config": {"CORE_UTILIZATION": observation["values"]["CORE_UTILIZATION"]},
         "flow_design_id": observation["values"]["DESIGN_NAME"]})
    candidate = replace(candidate, authority={"assets": {candidate.asset_id: proof.to_dict()}},
        binding_receipt_id=proof.binding_receipt_id,
        provenance={**candidate.provenance, "binding_digest": proof.binding_digest})
    return case, candidate


def test_observed_baseline_and_treatment_use_disposable_configuration(tmp_path):
    case, candidate = _observed_case(tmp_path)
    config = Path(case["project_dir"]) / "constraints/config.mk"
    original = config.read_bytes()
    baseline = execute_orfs_candidate(None, case, 1)
    treatment = execute_orfs_candidate(candidate, case, 1)
    assert baseline["outcome"] == "FAIL" and treatment["outcome"] == "PASS"
    assert treatment["metadata"]["observed_defaults_materialized"] is True
    assert treatment["metadata"]["configuration_observation_digest"] == case["flow_config_observation"]["receipt_digest"]
    assert config.read_bytes() == original


def test_fixed_candidate_requires_observation_before_execution(tmp_path):
    case, candidate = _observed_case(tmp_path)
    case.pop("flow_config_observation")
    with pytest.raises(OrfsCandidateOracleError, match="requires configuration observation"):
        execute_orfs_candidate(candidate, case, 1)


def test_observation_cannot_be_relabelled_to_another_value(tmp_path):
    case, candidate = _observed_case(tmp_path)
    case["flow_config_observation"]["values"]["CORE_UTILIZATION"] = "55"
    with pytest.raises(OrfsCandidateOracleError, match="replay mismatch"):
        execute_orfs_candidate(candidate, case, 1)


def test_binding_must_replay_against_observed_target(tmp_path):
    case, candidate = _observed_case(tmp_path)
    candidate = replace(candidate, binding_receipt_id="binding-not-the-observed-target")
    with pytest.raises(OrfsCandidateOracleError, match="does not match observed target"):
        execute_orfs_candidate(candidate, case, 1)
