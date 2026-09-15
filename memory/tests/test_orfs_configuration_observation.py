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
from test_orfs_runtime_resources import resource_binding


def _observed_case(tmp_path, *, pinned_klayout=False, pinned_resources=False):
    case = _fake_case(tmp_path)
    make = shutil.which("make")
    if not make:
        pytest.skip("Make unavailable")
    case.update(make_exe=make, python_exe=sys.executable)
    if pinned_klayout or pinned_resources:
        case["klayout_exe"] = sys.executable
    if pinned_resources:
        case["runtime_resources"] = resource_binding(tmp_path / "resources")
    scripts = Path(case["orfs_root"]) / "flow/scripts"
    scripts.mkdir()
    (scripts / "defaults.py").write_text("# fixture\n")
    (scripts / "variables.json").write_text("{}\n")
    (scripts.parent / "Makefile").write_text(
        f"include $(DESIGN_CONFIG)\nSCRIPTS_DIR := {scripts}\n")
    observation = probe_flow_config(
        Path(case["project_dir"]), Path(case["orfs_root"]), keys=("CORE_UTILIZATION",),
        **{key: Path(case[key]) for key in ("make_exe", "python_exe", "openroad_exe", "yosys_exe")},
        klayout_exe=Path(case["klayout_exe"]) if "klayout_exe" in case else None,
        runtime_resources=case.get("runtime_resources"))
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


def test_v2_pin_reaches_replay_staged_probe_and_actual_arm(tmp_path, monkeypatch):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_klayout=True)
    original = oracle._execute_arm
    environments = []
    def capture(*args):
        environments.append(dict(args[-1]))
        return original(*args)
    monkeypatch.setattr(oracle, "_execute_arm", capture)
    assert execute_orfs_candidate(None, case, 1)["outcome"] == "FAIL"
    assert execute_orfs_candidate(candidate, case, 1)["outcome"] == "PASS"
    assert len(environments) == 2
    assert all(env["KLAYOUT_CMD"] == str(Path(sys.executable).resolve()) for env in environments)
    assert case["flow_config_observation"]["version"] == "orfs-effective-config-probe-v2"


def test_v2_missing_case_pin_cannot_downgrade_observation(tmp_path, monkeypatch):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_klayout=True)
    case.pop("klayout_exe")
    monkeypatch.setattr(oracle, "_execute_arm", lambda *a: pytest.fail("EDA launched"))
    with pytest.raises(OrfsCandidateOracleError, match="replay mismatch"):
        execute_orfs_candidate(candidate, case, 1)


def test_v2_conflicting_execution_environment_rejected_before_eda(tmp_path, monkeypatch):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_klayout=True)
    case["environment"] = {"KLAYOUT_CMD": "/usr/bin/klayout"}
    monkeypatch.setattr(oracle, "_execute_arm", lambda *a: pytest.fail("EDA launched"))
    with pytest.raises(OrfsCandidateOracleError, match="pinned KLAYOUT_CMD"):
        execute_orfs_candidate(candidate, case, 1)


def test_v2_executor_override_cannot_replace_pin(tmp_path, monkeypatch):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_klayout=True)
    monkeypatch.setattr(oracle, "_execute_arm", lambda *a: pytest.fail("EDA launched"))
    with pytest.raises(OrfsCandidateOracleError, match="pinned KLAYOUT_CMD"):
        oracle.OrfsCandidateOracle(environment={"KLAYOUT_CMD": "/usr/bin/klayout"}).execute_candidate(
            candidate, case, 1)


@pytest.mark.parametrize("arm", ["NO_MEMORY", "ALWAYS_MEMORY", "APPLICABILITY_GATED", "CAUSAL_NO_SKILL"])
def test_v2_matching_executor_pin_reaches_each_physical_arm(tmp_path, monkeypatch, arm):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_klayout=True)
    pin = str(Path(sys.executable).resolve())
    original = oracle._execute_arm
    def capture(*args):
        assert args[-1]["KLAYOUT_CMD"] == pin
        return original(*args)
    monkeypatch.setattr(oracle, "_execute_arm", capture)
    adapter = oracle.OrfsCandidateOracle(environment={"KLAYOUT_CMD": pin})
    result = adapter.execute_policy_arm(arm, None if arm == "NO_MEMORY" else candidate, case, 1)
    assert result["metadata"]["policy_arm"] == arm


def test_v2_tool_changed_after_replay_is_rejected_before_actual_arm(tmp_path, monkeypatch):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_klayout=True)
    tool = tmp_path / "private-klayout"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    case["klayout_exe"] = str(tool)
    case["flow_config_observation"] = probe_flow_config(
        Path(case["project_dir"]), Path(case["orfs_root"]), keys=("CORE_UTILIZATION",),
        **{key: Path(case[key]) for key in ("make_exe", "python_exe", "openroad_exe", "yosys_exe")},
        klayout_exe=tool)
    original = oracle._copy_project
    def changing(*args):
        original(*args)
        tool.write_text(tool.read_text() + "# changed after replay\n")
    monkeypatch.setattr(oracle, "_copy_project", changing)
    monkeypatch.setattr(oracle, "_execute_arm", lambda *a: pytest.fail("EDA launched"))
    with pytest.raises(OrfsCandidateOracleError, match="tool pins changed"):
        execute_orfs_candidate(candidate, case, 1)


@pytest.mark.parametrize("arm", ["NO_MEMORY", "ALWAYS_MEMORY", "APPLICABILITY_GATED", "CAUSAL_NO_SKILL"])
def test_v3_resources_reach_replay_staging_and_each_physical_arm(tmp_path, monkeypatch, arm):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_resources=True)
    expected = case["runtime_resources"]["environment"]
    original = oracle._execute_arm
    def capture(*args):
        assert all(args[-1][key] == value for key, value in expected.items())
        return original(*args)
    monkeypatch.setattr(oracle, "_execute_arm", capture)
    result = oracle.OrfsCandidateOracle(environment=dict(expected)).execute_policy_arm(
        arm, None if arm == "NO_MEMORY" else candidate, case, 1)
    assert result["metadata"]["runtime_resource_bytes_verified"] is True
    assert result["metadata"]["parent_launch_binding_verified"] is False
    assert result["metadata"]["native_closure_proven"] is False
    assert case["flow_config_observation"]["version"] == "orfs-effective-config-probe-v3"


@pytest.mark.parametrize("source", ["case", "executor"])
@pytest.mark.parametrize("key", ["TERM", "TERMINFO", "TERMINFO_DIRS", "OPENSSL_CONF"])
def test_v3_conflicting_resource_environment_rejected_before_eda(tmp_path, monkeypatch, source, key):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_resources=True)
    monkeypatch.setattr(oracle, "_execute_arm", lambda *a: pytest.fail("EDA launched"))
    if source == "case":
        case["environment"] = {key: "/unbound/host"}
        adapter = oracle.OrfsCandidateOracle()
    else:
        adapter = oracle.OrfsCandidateOracle(environment={key: "/unbound/host"})
    with pytest.raises(OrfsCandidateOracleError, match="pinned runtime resource"):
        adapter.execute_candidate(candidate, case, 1)


def test_v3_missing_binding_cannot_downgrade_observation(tmp_path, monkeypatch):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_resources=True)
    case.pop("runtime_resources")
    monkeypatch.setattr(oracle, "_execute_arm", lambda *a: pytest.fail("EDA launched"))
    with pytest.raises(OrfsCandidateOracleError, match="replay mismatch"):
        execute_orfs_candidate(candidate, case, 1)


@pytest.mark.parametrize("observed", [False, True])
def test_resource_changed_during_staging_rejected_before_eda(tmp_path, monkeypatch, observed):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    if observed:
        case, candidate = _observed_case(tmp_path, pinned_resources=True)
    else:
        case, candidate = _fake_case(tmp_path), _candidate()
        case["runtime_resources"] = resource_binding(tmp_path / "resources")
    original = oracle._copy_project
    def changing(*args):
        original(*args)
        resource = Path(case["runtime_resources"]["environment"]["OPENSSL_CONF"])
        resource.write_text(resource.read_text() + "# changed\n")
    monkeypatch.setattr(oracle, "_copy_project", changing)
    monkeypatch.setattr(oracle, "_execute_arm", lambda *a: pytest.fail("EDA launched"))
    with pytest.raises(ValueError, match="resource bytes changed or SHA256"):
        execute_orfs_candidate(candidate, case, 1)


def test_resource_changed_by_flow_cannot_publish_a_success_receipt(tmp_path, monkeypatch):
    from tehm.evaluation import orfs_candidate_oracle as oracle
    case, candidate = _observed_case(tmp_path, pinned_resources=True)
    original = oracle._execute_arm
    def changing(*args):
        result = original(*args)
        resource = Path(case["runtime_resources"]["environment"]["OPENSSL_CONF"])
        resource.write_text(resource.read_text() + "# changed during flow\n")
        return result
    monkeypatch.setattr(oracle, "_execute_arm", changing)
    with pytest.raises(ValueError, match="resource bytes changed or SHA256"):
        execute_orfs_candidate(candidate, case, 1)


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


def test_measurement_bound_candidate_replays_exact_observed_binding(tmp_path):
    case, candidate = _observed_case(tmp_path)
    values = case["flow_config_observation"]["values"]
    measurement = {"scope": "route", "contract_digest": "sha256:measurement"}
    action = {**candidate.concrete_action, "payload": {
        **candidate.concrete_action["payload"],
        "measurement_contract_digest": measurement["contract_digest"],
    }}
    context = {
        "flow_design_id": values["DESIGN_NAME"],
        "flow_config": {"CORE_UTILIZATION": values["CORE_UTILIZATION"]},
        "target_scope": measurement["scope"],
        "measurement_contract_digest": measurement["contract_digest"],
    }
    asset = {
        "asset_id": candidate.asset_id,
        "definition": {"action": action, "measurement_contract": measurement},
        "verifier_contract": {"measurement_contract": measurement},
        "compatibility": {"target_scope": measurement["scope"]},
    }
    proof = bind_flow_config(asset, candidate.knowledge_object_id, context)
    candidate = replace(
        candidate, concrete_action=action,
        authority={"assets": {candidate.asset_id: proof.to_dict()}},
        binding_receipt_id=proof.binding_receipt_id,
        provenance={**candidate.provenance,
                    "binding_digest": proof.binding_digest,
                    "flow_binding_replay": {
                        "context": context,
                        "measurement_contract": measurement,
                    }})
    result = execute_orfs_candidate(candidate, case, 1)
    assert result["outcome"] == "PASS"
    tampered = replace(candidate, provenance={
        **candidate.provenance,
        "flow_binding_replay": {
            **candidate.provenance["flow_binding_replay"],
            "context": {**context, "flow_config": {"CORE_UTILIZATION": "84"}},
        }})
    with pytest.raises(OrfsCandidateOracleError, match="does not match observation"):
        execute_orfs_candidate(tampered, case, 1)


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


def test_flow_failure_without_target_report_is_unknown(tmp_path):
    case, candidate = _observed_case(tmp_path)
    Path(case["run_flow_script"]).write_text("#!/bin/sh\nexit 2\n")
    result = execute_orfs_candidate(candidate, case, 1)
    assert result["compile_result"] == "FAIL"
    assert result["functional_result"] == "UNKNOWN"
    assert result["outcome"] == "UNKNOWN"
    assert result["metadata"]["target_not_observed"] is True


def test_binding_must_replay_against_observed_target(tmp_path):
    case, candidate = _observed_case(tmp_path)
    candidate = replace(candidate, binding_receipt_id="binding-not-the-observed-target")
    with pytest.raises(OrfsCandidateOracleError, match="does not match observed target"):
        execute_orfs_candidate(candidate, case, 1)


@pytest.mark.parametrize("use_memory", [False, True])
def test_explicit_artifacts_survive_success_and_failure(tmp_path, use_memory):
    case, candidate = _observed_case(tmp_path)
    destination = tmp_path / "retained"
    case["execution_artifacts_dir"] = str(destination)
    result = execute_orfs_candidate(candidate if use_memory else None, case, 1)
    retained = Path(result["metadata"]["execution_project_dir"])
    assert retained.is_relative_to(destination)
    assert (retained / "reports/route.json").is_file()
    assert (retained / "constraints/config.mk").is_file()
    assert result["metadata"]["execution_artifacts_retained"] is True
    with pytest.raises(FileExistsError):
        execute_orfs_candidate(candidate if use_memory else None, case, 1)


def test_artifact_destination_cannot_modify_source_tree(tmp_path):
    case, candidate = _observed_case(tmp_path)
    case["execution_artifacts_dir"] = str(Path(case["project_dir"]) / "new-evidence")
    with pytest.raises(OrfsCandidateOracleError, match="outside source project"):
        execute_orfs_candidate(candidate, case, 1)


def test_checker_crash_without_report_is_unknown_not_design_failure(tmp_path):
    case, candidate = _observed_case(tmp_path)
    Path(case["run_flow_script"]).write_text("#!/bin/sh\nexit 0\n")
    Path(case["fix_signoff_script"]).write_text("#!/bin/sh\necho checker-crashed >&2\nexit 1\n")
    result = execute_orfs_candidate(candidate, case, 1)
    assert result["compile_result"] == "PASS"
    assert result["outcome"] == "UNKNOWN"
    assert result["functional_result"] == "UNKNOWN"
    assert result["metadata"]["fix_rc"] == 1
    assert "checker-crashed" in result["metadata"]["fix_stderr_tail"]
