"""Manifest-driven real ORFS P12 runner tests."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from scripts.run_orfs_p12_cohort import P12OrfsRunError, run_p12_orfs_cohort
from tehm.evaluation import P12_ARMS, OrfsPairedCohortReceipt
from tehm.evaluation.orfs_candidate_oracle import _file_sha256, _source_binding, _source_inputs
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


def _candidate() -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id="orfs-p12-runner-candidate", resolved_state_id="state-route",
        knowledge_object_id="mk-route-relief@1", causal_path_ids=("path-route-relief",),
        asset_id="asset-route-relief", action_family="DENSITY_RELIEF",
        concrete_action={
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": "DENSITY_RELIEF",
            "payload": {"config_edits": {"CORE_UTILIZATION": "30"},
                        "rerun_from": "synth", "recheck": "route"},
        }, applicability_receipt_id="app-orfs-p12", binding_receipt_id="binding-orfs-p12",
        obligations=("ORFS_FLOW_PASS", "ORFS_ROUTE_PASS", "ORFS_SIGNOFF_PASS"),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "orfs-p12-runner-test"})


def _fake_case(tmp_path: Path) -> dict[str, str]:
    project = tmp_path / "project"
    (project / "constraints").mkdir(parents=True)
    (project / "rtl").mkdir()
    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = fake_route\n"
        "export PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 50\n")
    (project / "constraints" / "constraint.sdc").write_text(
        "create_clock -period 10 [get_ports clk]\n")
    (project / "rtl" / "fake.v").write_text("module fake; endmodule\n")
    external = tmp_path / "external.v"
    external.write_text("module external; endmodule\n")
    orfs = tmp_path / "orfs"
    (orfs / "flow").mkdir(parents=True)
    (orfs / "flow" / "Makefile").write_text("all:\n\t@true\n")
    pdk = tmp_path / "pdks"
    (pdk / "sky130A").mkdir(parents=True)
    openroad, yosys = tmp_path / "openroad", tmp_path / "yosys"
    for tool in (openroad, yosys):
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    run_flow = tmp_path / "run_orfs.sh"
    run_flow.write_text(
        "#!/usr/bin/env bash\nset -eu\n"
        "p=\"$1\"\nmkdir -p \"$p/reports\"\n"
        "if grep -q 'CORE_UTILIZATION = 30' \"$p/constraints/config.mk\"; then\n"
        "  printf '{\"status\":\"clean\"}\n' > \"$p/reports/route.json\"\n"
        "else\n  printf '{\"status\":\"violations\",\"total_violations\":1}\n' > \"$p/reports/route.json\"\nfi\n")
    run_flow.chmod(run_flow.stat().st_mode | stat.S_IXUSR)
    fix = tmp_path / "fix_signoff.sh"
    fix.write_text("#!/usr/bin/env bash\nexit 0\n")
    fix.chmod(fix.stat().st_mode | stat.S_IXUSR)
    source_inputs = _source_inputs([{"path": str(external), "sha256": _file_sha256(external)}])
    source_digest = _source_binding(project, source_inputs)
    return {
        "case_id": "orfs-fake-p12", "project_dir": str(project),
        "platform": "sky130hd", "target_check": "route",
        "run_flow_script": str(run_flow), "fix_signoff_script": str(fix),
        "orfs_root": str(orfs), "openroad_exe": str(openroad), "yosys_exe": str(yosys),
        "pdk_root": str(pdk), "toolchain_root": str(tmp_path),
        "toolchain_digest": "sha256:orfs-test-toolchain",
        "oracle_digest": "sha256:orfs-test-oracle", "source_digest": source_digest,
        "source_inputs": [{"path": entry["path"], "sha256": entry["sha256"]}
                           for entry in source_inputs],
        "platform_digest": "sha256:orfs-test-platform", "pdk_digest": "sha256:orfs-test-pdk",
    }


def test_manifest_runner_executes_four_arms_and_emits_replayable_receipt(tmp_path):
    candidate = _candidate()
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate.to_dict(), sort_keys=True))
    case = _fake_case(tmp_path)
    case.update({
        "case_id": "p12-runner-case", "lineage_id": "lineage-runner",
        "candidate_paths": {
            "NO_MEMORY": None,
            "ALWAYS_MEMORY": str(candidate_path),
            "APPLICABILITY_GATED": str(candidate_path),
            "CAUSAL_NO_SKILL": str(candidate_path),
        },
    })
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": "p12-orfs-cohort-manifest-v1",
        "campaign_id": "p12-runner",
        "candidate_budget": 3,
        "min_lineages": 1,
        "platform_digest": case["platform_digest"],
        "pdk_digest": case["pdk_digest"],
        "cases": [case],
    }, sort_keys=True))
    report = run_p12_orfs_cohort(manifest, output=tmp_path / "report.json")
    receipt = OrfsPairedCohortReceipt.from_dict(report["cohort_receipt"])
    direct_receipt = OrfsPairedCohortReceipt.from_dict(report)
    assert receipt.lineage_count == 1
    assert direct_receipt.receipt_digest == receipt.receipt_digest
    assert receipt.outcome_counts["NO_MEMORY"]["FAIL"] == 1
    assert receipt.outcome_counts["ALWAYS_MEMORY"]["PASS"] == 1
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False


def test_manifest_runner_requires_a_candidate_for_always_memory(tmp_path):
    case = _fake_case(tmp_path)
    case.update({
        "case_id": "p12-runner-case", "lineage_id": "lineage-runner",
        "candidate_paths": {arm: None for arm in P12_ARMS},
    })
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": "p12-orfs-cohort-manifest-v1", "campaign_id": "p12-runner",
        "candidate_budget": 3, "min_lineages": 1,
        "platform_digest": case["platform_digest"], "pdk_digest": case["pdk_digest"],
        "cases": [case],
    }))
    with pytest.raises(P12OrfsRunError, match="ALWAYS_MEMORY requires"):
        run_p12_orfs_cohort(manifest, output=tmp_path / "report.json")
