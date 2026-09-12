"""R3-8 source-bound ORFS input freeze tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_r3_orfs_interference_source_bound_inputs import (
    SourceBoundInterferenceInputError,
    _config_source_map,
    _file_sha256,
    _toolchain,
    _training_rtl_hashes,
)
from scripts.run_orfs_p12_cohort import (
    P12OrfsRunError,
    _digest,
    _sha256,
    _source_bound_authority,
)
from tehm.physical.utility_contracts import (
    P12_DENSITY_RELIEF_INTERFERENCE_NONREGRESSION_V1_ID,
)


def _project(root: Path, name: str, rtl: str, sdc: Path) -> Path:
    project = root / name
    (project / "constraints").mkdir(parents=True)
    rtl_path = project / f"{name}.v"
    rtl_path.write_text(rtl)
    (project / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\n"
        "export PLATFORM = sky130hs\n"
        f"export VERILOG_FILES = {rtl_path}\n"
        f"export SDC_FILE = {sdc}\n"
        "export CORE_UTILIZATION = 50\n")
    return project


def test_disjointness_uses_rtl_content_but_still_binds_shared_sdc(tmp_path):
    shared_sdc = tmp_path / "shared.sdc"
    shared_sdc.write_text("create_clock -period 10 [get_ports clk]\n")
    training = _project(tmp_path, "training", "module training; endmodule\n", shared_sdc)
    challenge_a = _project(tmp_path, "challenge_a", "module a; endmodule\n", shared_sdc)
    challenge_b = _project(tmp_path, "challenge_b", "module b; endmodule\n", shared_sdc)
    acquisitions = {"t0": {"before": str(training), "after": str(training)}}

    training_hashes = _training_rtl_hashes(acquisitions)
    assert training_hashes == {_file_sha256(training / "training.v")}
    assert _file_sha256(shared_sdc) not in training_hashes
    for challenge in (challenge_a, challenge_b):
        sources = _config_source_map(challenge)
        assert sources["SDC_FILE"] == (shared_sdc.resolve(),)
        assert _file_sha256(sources["VERILOG_FILES"][0]) not in training_hashes


def test_toolchain_rejects_non_executable_pins(tmp_path):
    directories = {}
    for name in ("orfs_root", "pdk_root", "toolchain_root"):
        path = tmp_path / name
        path.mkdir()
        directories[name] = str(path)
    files = {}
    for name in ("openroad_exe", "yosys_exe", "toolchain_manifest",
                 "make_exe", "python_exe", "run_flow_script",
                 "fix_signoff_script"):
        path = tmp_path / name
        path.write_text("pin\n")
        files[name] = str(path)
    raw = {
        **directories, **files, "platform": "sky130hs",
        **{name: "sha256:pin" for name in (
            "toolchain_digest", "oracle_digest", "platform_digest",
            "pdk_digest")},
    }
    with pytest.raises(SourceBoundInterferenceInputError,
                       match="openroad_exe is not executable"):
        _toolchain({"toolchain": raw})


def test_physical_harm_contract_requires_source_bound_authority(tmp_path):
    manifest = {
        "campaign_id": "r3-8",
        "utility_contract_id":
            P12_DENSITY_RELIEF_INTERFERENCE_NONREGRESSION_V1_ID,
    }
    with pytest.raises(P12OrfsRunError, match="requires input_authority"):
        _source_bound_authority(
            tmp_path / "manifest.json", manifest, {"challenge:a"})


def test_source_bound_authority_is_content_addressed(tmp_path):
    contract_id = P12_DENSITY_RELIEF_INTERFERENCE_NONREGRESSION_V1_ID
    contract_digest = "sha256:contract"
    authority = {
        "version": "r3-8-source-bound-orfs-input-authority-v1",
        "campaign_id": "r3-8",
        "utility_contract_id": contract_id,
        "utility_contract_digest": contract_digest,
        "source_disjoint_scope": "verilog_content_sha256",
        "cases": {"challenge:a": {}},
        "actual_router_used": True,
        "actual_selector_used": True,
        "actual_runtime_binding_used": True,
        "actual_candidate_builder_used": True,
        "eda_executed": False,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "memory_docs_submitted": False,
    }
    authority["authority_digest"] = _digest(authority)
    authority_path = tmp_path / "authority.json"
    authority_path.write_text(json.dumps(authority, sort_keys=True))
    manifest = {
        "campaign_id": "r3-8", "utility_contract_id": contract_id,
        "utility_contract_digest": contract_digest,
        "input_authority": {
            "path": str(authority_path), "sha256": _sha256(authority_path),
            "authority_digest": authority["authority_digest"],
        },
    }
    checked, ref = _source_bound_authority(
        tmp_path / "manifest.json", manifest, {"challenge:a"})
    assert checked["actual_router_used"] is True
    assert ref["authority_digest"] == authority["authority_digest"]

    authority["actual_router_used"] = False
    authority_path.write_text(json.dumps(authority, sort_keys=True))
    manifest["input_authority"]["sha256"] = _sha256(authority_path)
    with pytest.raises(P12OrfsRunError, match="content digest mismatch"):
        _source_bound_authority(
            tmp_path / "manifest.json", manifest, {"challenge:a"})


def test_source_bound_authority_rejects_malformed_case_coverage(tmp_path):
    contract_id = P12_DENSITY_RELIEF_INTERFERENCE_NONREGRESSION_V1_ID
    authority = {
        "version": "r3-8-source-bound-orfs-input-authority-v1",
        "campaign_id": "r3-8", "utility_contract_id": contract_id,
        "utility_contract_digest": "sha256:contract", "cases": [],
        "source_disjoint_scope": "verilog_content_sha256",
        "actual_router_used": True, "actual_selector_used": True,
        "actual_runtime_binding_used": True,
        "actual_candidate_builder_used": True, "eda_executed": False,
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
    }
    authority["authority_digest"] = _digest(authority)
    path = tmp_path / "authority.json"
    path.write_text(json.dumps(authority, sort_keys=True))
    manifest = {
        "campaign_id": "r3-8", "utility_contract_id": contract_id,
        "utility_contract_digest": "sha256:contract",
        "input_authority": {"path": str(path), "sha256": _sha256(path),
                            "authority_digest": authority["authority_digest"]},
    }
    with pytest.raises(P12OrfsRunError, match="campaign/case coverage"):
        _source_bound_authority(
            tmp_path / "manifest.json", manifest, {"challenge:a"})
