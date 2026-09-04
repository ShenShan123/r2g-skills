"""Unit checks for the explicit external-ORFS challenge producer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_r3_orfs_interference_challenge import (
    OrfsInterferenceChallengeError, _candidate, _config_values,
)
import scripts.run_r3_orfs_interference_challenge as challenge


def _project(tmp_path: Path, *, include_sources: bool = True) -> Path:
    project = tmp_path / "orfs-project"
    (project / "constraints").mkdir(parents=True)
    source = tmp_path / "source.v"
    source.write_text("module source; endmodule\n")
    files = f"export VERILOG_FILES = {source}\n" if include_sources else ""
    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = uart\n"
        "export PLATFORM = sky130hs\n" + files)
    return project


def test_config_parser_requires_explicit_external_verilog_inputs(tmp_path):
    project = _project(tmp_path)
    assert _config_values(project)[:2] == ("uart", "sky130hs")
    assert "source.v" in _config_values(project)[2]

    missing = _project(tmp_path / "missing", include_sources=False)
    with pytest.raises(OrfsInterferenceChallengeError, match="VERILOG_FILES"):
        _config_values(missing)


def test_challenge_candidate_is_evaluation_only_config_delta():
    candidate = _candidate("uart", core_utilization="99")
    assert candidate.evaluation_only is True
    assert candidate.concrete_action["domain"] == "flow.CONFIG_DELTA"
    assert candidate.concrete_action["payload"]["config_edits"] == {
        "CORE_UTILIZATION": "99"}
    assert candidate.provenance["canonical_memory_mutation"] == "none"


def test_failed_reason_derivation_preserves_completed_cohort(tmp_path, monkeypatch):
    """A complete but non-interfering cohort must remain replayable on failure."""
    projects = []
    for index in range(2):
        project = tmp_path / f"project-{index}"
        (project / "constraints").mkdir(parents=True)
        source = tmp_path / f"source-{index}.v"
        source.write_text(f"module source_{index}; endmodule\n")
        (project / "constraints" / "config.mk").write_text(
            "export DESIGN_NAME = uart\n"
            "export PLATFORM = sky130hs\n"
            f"export VERILOG_FILES = {source}\n")
        (project / "constraints" / "constraint.sdc").write_text(
            "current_design uart\n")
        projects.append(project)

    toolchain = tmp_path / "toolchain"
    pdk = tmp_path / "pdks"
    toolchain.mkdir()
    pdk.mkdir()
    orfs = tmp_path / "orfs"
    (orfs / "flow").mkdir(parents=True)
    for name in ("openroad", "yosys"):
        binary = toolchain / name
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
    manifest = toolchain / "manifest.json"
    manifest.write_text("{}\n")

    class FakeCohort:
        case_receipts = {"case-0": object(), "case-1": object()}
        lineage_count = 2
        outcome_counts = {"NO_MEMORY": {"PASS": 2}}
        receipt_digest = "sha256:" + "1" * 64

        def to_dict(self):
            return {"campaign_id": "fake", "case_receipts": {}}

    monkeypatch.setattr(challenge, "execute_orfs_paired_cohort",
                        lambda *args, **kwargs: FakeCohort())
    monkeypatch.setattr(challenge, "derive_memory_interference_reason",
                        lambda *args, **kwargs: None)
    artifacts = tmp_path / "artifacts"
    digest = "sha256:" + "0" * 64
    with pytest.raises(OrfsInterferenceChallengeError, match="reason derivation failed"):
        challenge.run(
            projects, artifacts=artifacts, campaign_id="failure-preserve",
            lineages=["lineage-a", "lineage-b"],
            orfs_root=orfs, openroad_exe=toolchain / "openroad",
            yosys_exe=toolchain / "yosys", pdk_root=pdk,
            toolchain_root=toolchain, toolchain_manifest=manifest,
            toolchain_digest=digest, oracle_digest=digest,
            platform_digest=digest, pdk_digest=digest)

    assert (artifacts / "receipts" / "campaign_manifest.json").is_file()
    assert (artifacts / "receipts" / "cases.json").is_file()
    assert (artifacts / "receipts" / "cohort.json").is_file()
    reasons = json.loads(
        (artifacts / "receipts" / "reason_derivation.json").read_text())
    assert reasons["errors"] == {
        "case-0": "case did not produce MEMORY_INTERFERENCE",
        "case-1": "case did not produce MEMORY_INTERFERENCE",
    }
    failure = json.loads((artifacts / "failure.json").read_text())
    assert failure["status"] == "REASON_DERIVATION_FAILED"
    assert failure["cohort_receipt_digest"] == FakeCohort.receipt_digest
    assert failure["memory_docs_submitted"] is False
