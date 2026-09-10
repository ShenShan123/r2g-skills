"""P12-D ORFS executor boundary tests with a deterministic fake R2G flow."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from tehm.evaluation.candidate_executor import P12_ARMS, execute_candidate, execute_paired_candidates
from tehm.evaluation.counterfactual_oracle import (
    COUNTERFACTUAL_SCOPE, counterfactual_oracle_complete,
)
from tehm.evaluation.orfs_candidate_oracle import (
    OrfsCandidateOracle, OrfsCandidateOracleError, _environment, _file_sha256,
    _source_binding, _source_inputs,
)
from tehm.evaluation.orfs_cohort import (
    OrfsCohortError, OrfsPairedCohortReceipt, execute_orfs_paired_cohort,
)
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


def _candidate() -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id="orfs-p12-config-candidate",
        resolved_state_id="state-route",
        knowledge_object_id="mk-route-relief@1",
        causal_path_ids=("path-route-relief",), asset_id="asset-route-relief",
        action_family="DENSITY_RELIEF",
        concrete_action={
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": "DENSITY_RELIEF",
            "payload": {"config_edits": {"CORE_UTILIZATION": "30"},
                        "rerun_from": "synth", "recheck": "route"},
        },
        applicability_receipt_id="app-orfs-p12", binding_receipt_id="binding-orfs-p12",
        obligations=("ORFS_FLOW_PASS", "ORFS_ROUTE_PASS", "ORFS_SIGNOFF_PASS"),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "orfs-controlled-test"},
    )


def _fake_case(tmp_path: Path) -> dict[str, str]:
    project = tmp_path / "project"
    (project / "constraints").mkdir(parents=True)
    (project / "rtl").mkdir()
    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = fake_route\n"
        "export PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 50\n")
    (project / "constraints" / "constraint.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    (project / "rtl" / "fake.v").write_text("module fake; endmodule\n")
    external = tmp_path / "external.v"
    external.write_text("module external; endmodule\n")

    orfs = tmp_path / "orfs"
    (orfs / "flow").mkdir(parents=True)
    (orfs / "flow" / "Makefile").write_text("all:\n\t@true\n")
    pdk = tmp_path / "pdks"
    (pdk / "sky130A").mkdir(parents=True)
    openroad = tmp_path / "openroad"
    yosys = tmp_path / "yosys"
    for tool in (openroad, yosys):
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)

    run_flow = tmp_path / "run_orfs.sh"
    run_flow.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "p=\"$1\"\n"
        "mkdir -p \"$p/reports\"\n"
        "if grep -q 'CORE_UTILIZATION = 30' \"$p/constraints/config.mk\"; then\n"
        "  printf '{\"status\":\"clean\"}\\n' > \"$p/reports/route.json\"\n"
        "else\n"
        "  printf '{\"status\":\"violations\",\"total_violations\":1}\\n' > \"$p/reports/route.json\"\n"
        "fi\n")
    run_flow.chmod(run_flow.stat().st_mode | stat.S_IXUSR)
    fix = tmp_path / "fix_signoff.sh"
    fix.write_text("#!/usr/bin/env bash\nexit 0\n")
    fix.chmod(fix.stat().st_mode | stat.S_IXUSR)
    source_inputs = _source_inputs([{"path": str(external), "sha256": _file_sha256(external)}])
    source_digest = _source_binding(project, source_inputs)
    return {
        "case_id": "orfs-fake-p12",
        "project_dir": str(project), "platform": "sky130hd", "target_check": "route",
        "run_flow_script": str(run_flow), "fix_signoff_script": str(fix),
        "orfs_root": str(orfs), "openroad_exe": str(openroad), "yosys_exe": str(yosys),
        "pdk_root": str(pdk), "toolchain_root": str(tmp_path),
        "toolchain_digest": "sha256:orfs-test-toolchain",
        "oracle_digest": "sha256:orfs-test-oracle",
        "source_digest": source_digest,
        "source_inputs": [{"path": entry["path"], "sha256": entry["sha256"]}
                           for entry in source_inputs],
        "platform_digest": "sha256:orfs-test-platform",
        "pdk_digest": "sha256:orfs-test-pdk",
    }


def test_orfs_candidate_uses_temp_project_and_real_executor_contract(tmp_path):
    case = _fake_case(tmp_path)
    before = Path(case["project_dir"]) / "constraints" / "config.mk"
    original = before.read_bytes()
    receipt = execute_candidate(
        _candidate(), case, oracle=OrfsCandidateOracle(), budget=3)
    assert receipt.outcome == "PASS"
    assert receipt.compile_result == "PASS"
    assert receipt.functional_result == "PASS"
    assert receipt.signoff_result == "UNKNOWN"  # route-only is not full signoff
    assert receipt.produced_transition_id is None
    oracle_metadata = receipt.metadata["oracle_metadata"]
    assert oracle_metadata["action_applied"] is True
    assert oracle_metadata["config_before_digest"] != oracle_metadata["config_after_digest"]
    assert before.read_bytes() == original


def test_orfs_fixed_constraint_counterfactual_is_shadow_complete(tmp_path):
    case = _fake_case(tmp_path)
    case["target_check"] = COUNTERFACTUAL_SCOPE
    run_flow = Path(case["run_flow_script"])
    manifest = {
        "reports": {
            "route.json": {"present": True, "sha256": "a" * 64,
                           "status": "clean"},
            "drc.json": {"present": True, "sha256": "b" * 64,
                         "status": "clean"},
            "lvs.json": {"present": True, "sha256": "c" * 64,
                         "status": "clean"},
            "rcx.json": {"present": True, "sha256": "d" * 64,
                         "status": "complete"},
            "timing_check.json": {"present": True, "sha256": "e" * 64,
                                  "tier": "clean"},
        },
        "confirming_run": {"consensus": True},
        "platform_capability": {"strict_signoff_ready": True},
        "constraint": {"final_timing_tier": "clean", "sdc_sha256": "f" * 64},
        "strict_missing": [
            "constraint: fmax_search winner (reports/fmax_search.json status=ok)"
        ],
        "strict_clean": False,
    }
    run_flow.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "p=\"$1\"\n"
        "mkdir -p \"$p/reports\"\n"
        "printf '{\"status\":\"clean\"}\\n' > \"$p/reports/route.json\"\n"
        "printf '{\"status\":\"clean\"}\\n' > \"$p/reports/drc.json\"\n"
        "printf '{\"status\":\"clean\"}\\n' > \"$p/reports/lvs.json\"\n"
        "printf '{\"status\":\"complete\"}\\n' > \"$p/reports/rcx.json\"\n"
        "printf '{\"tier\":\"clean\"}\\n' > \"$p/reports/timing_check.json\"\n"
        f"printf '%s\\n' '{json.dumps(manifest)}' > \"$p/reports/signoff_manifest.json\"\n")
    receipt = execute_candidate(
        _candidate(), case, oracle=OrfsCandidateOracle(), budget=3)
    assert receipt.outcome == "PASS"
    assert receipt.functional_result == "PASS"
    assert receipt.signoff_result == "UNKNOWN"
    assert counterfactual_oracle_complete(receipt) is True
    claim = receipt.metadata["oracle_metadata"]["counterfactual_oracle"]
    assert claim["strict_signoff_claim"] is False
    assert claim["production_eligible"] is False


def test_orfs_paired_arms_hold_baseline_and_candidate_apart(tmp_path):
    case = _fake_case(tmp_path)
    candidate = _candidate()
    bundle = execute_paired_candidates(
        case, {arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS},
        oracle=OrfsCandidateOracle(), budget=3)
    assert bundle.arm_receipts["NO_MEMORY"].outcome == "FAIL"
    assert all(bundle.arm_receipts[arm].outcome == "PASS" for arm in P12_ARMS[1:])


def test_orfs_paired_arms_retain_distinct_policy_artifacts(tmp_path):
    case = _fake_case(tmp_path)
    root = tmp_path / "retained-arms"
    case["execution_artifacts_root"] = str(root)
    candidate = _candidate()
    bundle = execute_paired_candidates(
        case, {arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS},
        oracle=OrfsCandidateOracle(), budget=3,
        routing_decision="NO_SKILL", no_skill_reason="STATE_SHIFT",
        state_shift_receipt_id="state-shift-receipt",
        routing_receipt_id="routing-receipt")
    assert bundle.arm_receipts["NO_MEMORY"].outcome == "FAIL"
    assert bundle.arm_receipts["CAUSAL_NO_SKILL"].outcome == "FAIL"
    assert bundle.arm_receipts["CAUSAL_NO_SKILL"].source == "no_memory"
    assert bundle.arm_receipts["ALWAYS_MEMORY"].outcome == "PASS"
    assert bundle.arm_receipts["APPLICABILITY_GATED"].outcome == "PASS"
    retained = set()
    for arm in P12_ARMS:
        metadata = bundle.arm_receipts[arm].metadata["oracle_metadata"]
        assert metadata["policy_arm"] == arm
        path = Path(metadata["execution_project_dir"])
        assert path.is_relative_to(root / arm.lower())
        assert (path / "reports/route.json").is_file()
        retained.add(path)
    assert len(retained) == len(P12_ARMS)


def test_orfs_artifact_root_requires_policy_arm_dispatch(tmp_path):
    case = _fake_case(tmp_path)
    case["execution_artifacts_root"] = str(tmp_path / "retained-arms")
    with pytest.raises(OrfsCandidateOracleError, match="policy-arm execution"):
        from tehm.evaluation.orfs_candidate_oracle import execute_orfs_candidate
        execute_orfs_candidate(None, case, 1)


def test_orfs_adapter_rejects_non_flow_candidate_without_manifest_read(tmp_path):
    case = _fake_case(tmp_path)
    bad = _candidate()
    object.__setattr__(bad, "concrete_action", {
        "domain": "rtl.GUARD_STRENGTHEN", "payload": {"add_condition": "ack"}})
    case["manifest"] = "/missing/manifest.json"
    with pytest.raises(OrfsCandidateOracleError, match="flow.CONFIG_DELTA"):
        # Call the adapter directly so malformed action is not downgraded to
        # generic UNKNOWN by candidate_executor's injected-oracle firewall.
        from tehm.evaluation.orfs_candidate_oracle import execute_orfs_candidate
        execute_orfs_candidate(bad, case, 3)


def test_orfs_adapter_rejects_environment_pin_override(tmp_path):
    case = _fake_case(tmp_path)
    case["environment"] = {"ORFS_ROOT": "/different/orfs"}
    with pytest.raises(OrfsCandidateOracleError, match="pinned key ORFS_ROOT"):
        from tehm.evaluation.orfs_candidate_oracle import execute_orfs_candidate
        execute_orfs_candidate(_candidate(), case, 3)


def test_orfs_environment_clears_incompatible_python_launcher_state(tmp_path):
    case = _fake_case(tmp_path)
    environment = _environment(case)
    assert environment["PYTHONHOME"] == ""
    assert environment["PYTHONPATH"] == ""
    assert environment["PYTHONEXECUTABLE"] == ""
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_orfs_adapter_binds_external_source_inputs(tmp_path):
    case = _fake_case(tmp_path)
    external = tmp_path / "external.v"
    external.write_text("module external; endmodule\n")
    case["source_inputs"] = [{"path": str(external), "sha256": _file_sha256(external)}]
    case["source_digest"] = _source_binding(
        Path(case["project_dir"]), _source_inputs(case["source_inputs"]))
    receipt = execute_candidate(
        _candidate(), case, oracle=OrfsCandidateOracle(), budget=3)
    assert receipt.outcome == "PASS"
    assert receipt.metadata["oracle_metadata"]["source_digest"].startswith("sha256:")
    external.write_text("module external_changed; endmodule\n")
    with pytest.raises(OrfsCandidateOracleError, match="source input digest mismatch"):
        from tehm.evaluation.orfs_candidate_oracle import execute_orfs_candidate
        execute_orfs_candidate(_candidate(), case, 3)


def test_orfs_cohort_executes_and_replays_fixed_four_arm_bundle(tmp_path):
    case = _fake_case(tmp_path)
    case["no_skill_reason"] = "NO_MATCH"
    candidate = _candidate()
    arms = {arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS}
    bundle = execute_orfs_paired_cohort(
        [case], {case["case_id"]: arms}, campaign_id="orfs-cohort-test",
        campaign_manifest_digest="sha256:orfs-test-manifest",
        platform_digest=case["platform_digest"], pdk_digest=case["pdk_digest"],
        oracle=OrfsCandidateOracle(), budget=3,
        toolchain_digest=case["toolchain_digest"], oracle_digest=case["oracle_digest"])
    assert isinstance(bundle, OrfsPairedCohortReceipt)
    assert bundle.outcome_counts["NO_MEMORY"]["FAIL"] == 1
    assert bundle.no_skill_reason_counts == {
        "NO_MATCH": 1, "STATE_SHIFT": 0, "RISK": 0}
    assert bundle.lineage_count == 1
    assert bundle.lineage_ids == {case["case_id"]: case["case_id"]}
    replay = OrfsPairedCohortReceipt.from_dict({
        **bundle.to_dict(), "receipt_digest": bundle.receipt_digest})
    assert replay.to_dict() == bundle.to_dict()
    legacy = bundle.to_dict()
    legacy.pop("lineage_ids")
    legacy.pop("no_skill_reason_counts")
    for value in legacy["case_receipts"].values():
        for key in ("no_skill_reason", "state_shift_receipt_id",
                    "risk_receipt_id", "lineage_id", "routing_receipt_id"):
            value.pop(key, None)
    legacy_replay = OrfsPairedCohortReceipt.from_dict({
        **legacy, "receipt_digest": bundle.legacy_receipt_digest})
    assert legacy_replay.legacy_receipt_digest == bundle.legacy_receipt_digest


def test_orfs_cohort_min_lineages_is_preflighted(tmp_path):
    first = _fake_case(tmp_path / "first")
    second = _fake_case(tmp_path / "second")
    second["case_id"] = "orfs-fake-p12-second"
    second_project = Path(second["project_dir"])
    (second_project / "rtl" / "fake.v").write_text("module fake_second; endmodule\n")
    second["source_digest"] = _source_binding(
        second_project, _source_inputs(second["source_inputs"]))
    first["lineage_id"] = "lineage-a"
    second["lineage_id"] = "lineage-b"
    candidate = _candidate()
    arms = {
        case["case_id"]: {
            arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS}
        for case in (first, second)
    }
    bundle = execute_orfs_paired_cohort(
        [first, second], arms, campaign_id="orfs-lineage-gate",
        campaign_manifest_digest="sha256:orfs-test-manifest",
        platform_digest=first["platform_digest"], pdk_digest=first["pdk_digest"],
        oracle=OrfsCandidateOracle(), budget=3,
        toolchain_digest=first["toolchain_digest"],
        oracle_digest=first["oracle_digest"], min_lineages=2)
    assert bundle.lineage_count == 2
    assert bundle.lineage_ids == {
        first["case_id"]: "lineage-a", second["case_id"]: "lineage-b"}

    missing = _fake_case(tmp_path / "missing")
    missing["case_id"] = "orfs-fake-p12-missing-lineage"
    with pytest.raises(OrfsCohortError, match="explicit lineage_id"):
        execute_orfs_paired_cohort(
            [first, missing],
            {first["case_id"]: arms[first["case_id"]],
             missing["case_id"]: arms[first["case_id"]]},
            campaign_id="orfs-lineage-gate-missing",
            campaign_manifest_digest="sha256:orfs-test-manifest",
            platform_digest=first["platform_digest"], pdk_digest=first["pdk_digest"],
            oracle=OrfsCandidateOracle(), budget=3,
            toolchain_digest=first["toolchain_digest"],
            oracle_digest=first["oracle_digest"], min_lineages=2)


def test_orfs_cohort_rejects_missing_explicit_external_sources(tmp_path):
    case = _fake_case(tmp_path)
    case.pop("source_inputs")
    with pytest.raises(OrfsCohortError, match="explicit source_inputs"):
        execute_orfs_paired_cohort(
            [case], {case["case_id"]: {arm: None for arm in P12_ARMS}},
            campaign_id="orfs-cohort-test",
            campaign_manifest_digest="sha256:orfs-test-manifest",
            platform_digest=case["platform_digest"], pdk_digest=case["pdk_digest"],
            oracle=OrfsCandidateOracle(), budget=3,
            toolchain_digest=case["toolchain_digest"], oracle_digest=case["oracle_digest"])


def test_orfs_cohort_rejects_same_source_content_under_distinct_paths(tmp_path):
    first = _fake_case(tmp_path / "first")
    second = _fake_case(tmp_path / "second")
    second["case_id"] = "orfs-fake-p12-second"
    with pytest.raises(OrfsCohortError, match="source content is not disjoint"):
        execute_orfs_paired_cohort(
            [first, second],
            {first["case_id"]: {arm: None for arm in P12_ARMS},
             second["case_id"]: {arm: None for arm in P12_ARMS}},
            campaign_id="orfs-cohort-test",
            campaign_manifest_digest="sha256:orfs-test-manifest",
            platform_digest=first["platform_digest"], pdk_digest=first["pdk_digest"],
            oracle=OrfsCandidateOracle(), budget=3,
            toolchain_digest=first["toolchain_digest"],
            oracle_digest=first["oracle_digest"])
