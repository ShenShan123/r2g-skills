"""Batch-0 lane stays external/staging until independent authority admits it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tehm import db as tehm_db
from tehm.batch_lane import (
    BatchLaneError,
    assess_full_oracle,
    assert_snapshots_unchanged,
    import_support_to_staging,
    read_external_observations,
    require_staging_destination,
    validate_canonical_import_authority,
    write_external_observations,
)


def test_orfs_config_rewrite_removes_replaced_make_continuations():
    from scripts.run_orfs_diversity_campaign import _apply_edits

    source = (
        "export VERILOG_FILES = $(wildcard src/*.sv) \\\n"
        "    src/clock_gate.v\n"
        "export VERILOG_INCLUDE_DIRS = \\\n"
        "    include/\n"
    )
    rewritten = _apply_edits(source, {"VERILOG_FILES": "/abs/top.sv"})
    assert "export VERILOG_FILES = /abs/top.sv\n" in rewritten
    assert "src/clock_gate.v" not in rewritten
    assert "export VERILOG_INCLUDE_DIRS = \\\n    include/\n" in rewritten


def test_batch_phase_reports_merge_allowlisted_subsets(tmp_path):
    from scripts.run_orfs_batch0 import _merge_phase_results

    path = tmp_path / "phase.json"
    _merge_phase_results(path, [{"project": "/a", "status": "old"}])
    report = _merge_phase_results(path, [
        {"project": "/b", "status": "new"},
        {"project": "/a", "status": "updated"},
    ])
    assert report["results"] == [
        {"project": "/a", "status": "updated"},
        {"project": "/b", "status": "new"},
    ]


def test_batch_prepare_requires_source_freeze(tmp_path):
    """A campaign cannot materialize evidence with an unbound source tree."""
    from scripts.run_orfs_batch0 import prepare

    with pytest.raises(BatchLaneError, match="source freeze is required"):
        prepare(
            tmp_path / "campaign",
            tmp_path / "orfs",
            tmp_path / "manifest.json",
            source_freeze=tmp_path / "campaign" / "source_freeze.json",
        )
from tehm.rtl.equivalence import YosysEquivalenceOracle


def test_staging_destination_is_campaign_local_and_not_canonical(tmp_path):
    root = tmp_path / "campaign"
    accepted = root / "staging" / "tehm.sqlite"
    assert require_staging_destination(accepted, campaign_root=root) == accepted.resolve()
    with pytest.raises(BatchLaneError, match="must be below"):
        require_staging_destination(tmp_path / "elsewhere.sqlite", campaign_root=root)


def test_snapshot_change_is_detected():
    before = [{"path": "/canonical", "exists": True, "sha256": "a"}]
    after = [{"path": "/canonical", "exists": True, "sha256": "b"}]
    with pytest.raises(BatchLaneError, match="canonical memory changed"):
        assert_snapshots_unchanged(before, after)


def test_full_oracle_is_conjunctive_and_missing_equivalence_is_incomplete(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    run = project / "backend" / "RUN_1"
    reports = project / "reports"
    constraints = project / "constraints"
    final = run / "final"
    for path in (reports, constraints, final):
        path.mkdir(parents=True, exist_ok=True)
    rtl = project / "top.v"
    rtl.write_text("module top(input clk); endmodule\n")
    (constraints / "config.mk").write_text("export DESIGN_NAME = top\n")
    (constraints / "constraint.sdc").write_text("current_design top\n")
    (run / "run-meta.json").write_text(json.dumps({"run_tag": "RUN_1", "make_status": 0}))
    (run / "stage_log.jsonl").write_text("".join(
        json.dumps({"stage": stage, "status": 0}) + "\n"
        for stage in ("synth", "route", "finish")))
    (final / "6_final.def").write_text("VERSION 5.8 ;\n")
    (final / "6_final.gds").write_bytes(b"gds")
    documents = {
        "route.json": {"status": "clean"},
        "timing_check.json": {"tier": "clean"},
        "drc.json": {"status": "clean"},
        "lvs.json": {"status": "clean"},
        "ppa.json": {"timing": {"setup_wns": 0.1, "setup_tns": 0.0},
                     "area": {"design_area_um2": 12.0},
                     "power": {"total": 0.01}},
        "strict_signoff.json": {"status": "pass"},
        "features_stats.json": {"status": "ok"},
        "batch_graph_context.json": {"status": "complete", "digest": "graph-digest"},
    }
    for name, value in documents.items():
        (reports / name).write_text(json.dumps(value))

    class Context:
        status = "complete"

        def to_dict(self):
            return {"status": "complete", "digest": "graph-digest"}

    monkeypatch.setattr("tehm.batch_lane.load_defgraph_context",
                        lambda project, def_path: Context())
    result = assess_full_oracle(project, rtl_files=[rtl])
    assert result["complete"] is False
    assert result["checks"]["equivalence"] is False
    assert result["checks"]["toolchain_binding"] is False
    assert result["missing_oracles"] == ["equivalence", "artifact_digest"]


def test_full_oracle_extracts_area_from_orfs_geometry_payload():
    from tehm.batch_lane import _core_ppa_metrics

    metrics = _core_ppa_metrics({
        "summary": {"timing": {"setup_wns": 0.1, "setup_tns": 0.0},
                    "power": {"total_power_w": 0.01}},
        "geometry": {"die_area_um2": 123.5},
    })
    assert metrics["area_um2"] == 123.5


def test_full_oracle_rejects_post_prepare_input_mutation(tmp_path):
    from tehm.batch_lane import _input_binding, _timing_contract

    project = tmp_path / "project"
    constraints = project / "constraints"
    constraints.mkdir(parents=True)
    rtl = tmp_path / "top.v"
    rtl.write_text("module top(input clk); endmodule\n")
    (constraints / "config.mk").write_text("export DESIGN_NAME = top\n")
    (constraints / "constraint.sdc").write_text(
        "current_design top\nset clk_period 3.0\n")
    expected = _input_binding(project, [rtl])
    expected_timing = _timing_contract(project)
    assert assess_full_oracle(
        project, rtl_files=[rtl], expected_input_binding=expected,
        expected_timing_contract=expected_timing
    )["checks"]["input_binding"] is True
    assert assess_full_oracle(
        project, rtl_files=[rtl], expected_input_binding=expected,
        expected_timing_contract=expected_timing
    )["checks"]["timing_contract"] is True

    (constraints / "constraint.sdc").write_text(
        "current_design top\nset clk_period 2.5\n")
    result = assess_full_oracle(
        project, rtl_files=[rtl], expected_input_binding=expected,
        expected_timing_contract=expected_timing)
    assert result["checks"]["input_binding"] is False
    assert result["checks"]["timing_contract"] is False
    assert result["input_binding"]["actual"] != expected


def test_staging_import_only_accepts_complete_support_receipts(
        tmp_path, sample_record_dict):
    root = tmp_path / "campaign"
    observations = root / "external" / "observations.jsonl"
    support = {
        "receipt_id": "support-1", "version": "orfs-external-observation-v1",
        "case_id": "support", "lineage_id": "lineage-support",
        "platform": "sky130hs", "family": "DENSITY_RELIEF", "split": "support",
        "classification": "ELIGIBLE_POSITIVE", "learner_eligible": True,
        "before": {"graph": {
            "extractor_version": "def-graph-feature-context-v0.2",
            "design": "support", "platform": "sky130hs", "status": "complete",
            "dataset_tier": "strict_clean", "graph_features": {"num_cells": 1},
            "topology_rows": {}, "feature_health": {},
            "signoff_health": {"status": "pass"}, "def_sha256": "def-support",
            "feature_digests": {},
        }},
        "record": sample_record_dict, "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }
    calibration = {
        **support, "receipt_id": "calibration-1", "case_id": "calibration",
        "split": "calibration", "learner_eligible": False,
    }
    write_external_observations(observations, [support, calibration])
    result = import_support_to_staging(
        observations_path=observations,
        staging_db=root / "staging" / "tehm.sqlite",
        staging_artifacts=root / "staging" / "artifacts",
        campaign_root=root, campaign_id="batch0-test")
    assert [row["case_id"] for row in result["imported"]] == ["support"]
    assert result["excluded_external_only"] == 1
    assert result["canonical_memory_mutation"] == "none"
    conn = tehm_db.connect(root / "staging" / "tehm.sqlite")
    try:
        assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 1
        row = conn.execute(
            "SELECT graph_context_json,graph_context_digest "
            "FROM tehm_physical_effects").fetchone()
        assert '"design":"support"' in row["graph_context_json"]
        assert row["graph_context_digest"]
    finally:
        conn.close()


def test_staging_import_rolls_back_all_rows_on_late_receipt_failure(
        tmp_path, sample_record_dict):
    """A malformed later support row must not leave a partial staging DB."""
    root = tmp_path / "campaign"
    observations = root / "external" / "observations.jsonl"
    support = {
        "receipt_id": "support-a", "version": "orfs-external-observation-v1",
        "case_id": "a-support", "lineage_id": "lineage-a",
        "platform": "sky130hs", "family": "DENSITY_RELIEF", "split": "support",
        "classification": "ELIGIBLE_POSITIVE", "learner_eligible": True,
        "record": sample_record_dict, "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }
    malformed = {**support, "receipt_id": "support-z", "case_id": "z-broken",
                 "lineage_id": "lineage-z", "record": None}
    write_external_observations(observations, [support, malformed])
    with pytest.raises(BatchLaneError, match="complete positive evidence"):
        import_support_to_staging(
            observations_path=observations,
            staging_db=root / "staging" / "tehm.sqlite",
            staging_artifacts=root / "staging" / "artifacts",
            campaign_root=root, campaign_id="batch0-atomic")

    conn = tehm_db.connect(root / "staging" / "tehm.sqlite")
    try:
        assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tehm_physical_effects").fetchone()[0] == 0
    finally:
        conn.close()


def test_canonical_import_rolls_back_all_rows_on_late_receipt_failure(
        tmp_path, sample_record_dict, monkeypatch):
    """Authority selection cannot turn a partial canonical import into evidence."""
    from tehm.batch_lane import import_support_to_canonical

    root = tmp_path / "campaign"
    observations = root / "external" / "observations.jsonl"
    support = {
        "receipt_id": "canonical-a", "version": "orfs-external-observation-v1",
        "case_id": "a-support", "lineage_id": "lineage-a",
        "platform": "sky130hs", "family": "DENSITY_RELIEF", "split": "support",
        "classification": "ELIGIBLE_POSITIVE", "learner_eligible": True,
        "record": sample_record_dict, "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }
    malformed = {**support, "receipt_id": "canonical-z", "case_id": "z-broken",
                 "lineage_id": "lineage-z", "record": None}
    write_external_observations(observations, [support, malformed])
    staging = root / "staging" / "tehm.sqlite"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"staging-placeholder")
    canonical = root / "canonical" / "tehm.sqlite"
    monkeypatch.setattr(
        "tehm.batch_lane.validate_canonical_import_authority",
        lambda *args, **kwargs: None)
    with pytest.raises(BatchLaneError, match="non-importable observation"):
        import_support_to_canonical(
            observations_path=observations, staging_db=staging,
            canonical_db=canonical,
            canonical_artifacts=root / "canonical" / "artifacts",
            campaign_id="batch0-canonical-atomic",
            authority={"case_ids": ["a-support", "z-broken"]})

    conn = tehm_db.connect(canonical)
    try:
        assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tehm_physical_effects").fetchone()[0] == 0
    finally:
        conn.close()


def test_canonical_import_requires_learner_eligible_support_row(
        tmp_path, sample_record_dict, monkeypatch):
    from tehm.batch_lane import import_support_to_canonical

    root = tmp_path / "campaign"
    observations = root / "external" / "observations.jsonl"
    row = {
        "receipt_id": "heldout-positive", "version": "orfs-external-observation-v1",
        "case_id": "heldout-positive", "lineage_id": "lineage-heldout",
        "platform": "sky130hs", "family": "DENSITY_RELIEF", "split": "support",
        "classification": "ELIGIBLE_POSITIVE", "learner_eligible": False,
        "record": sample_record_dict, "canonical_memory_mutation": "none",
    }
    write_external_observations(observations, [row])
    staging = root / "staging" / "tehm.sqlite"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"staging-placeholder")
    monkeypatch.setattr(
        "tehm.batch_lane.validate_canonical_import_authority",
        lambda *args, **kwargs: None)
    with pytest.raises(BatchLaneError, match="non-importable observation"):
        import_support_to_canonical(
            observations_path=observations, staging_db=staging,
            canonical_db=root / "canonical" / "tehm.sqlite",
            canonical_artifacts=root / "canonical" / "artifacts",
            campaign_id="batch0-learner-firewall",
            authority={"case_ids": ["heldout-positive"]})


def test_canonical_import_rejects_unknown_authority_case(tmp_path, monkeypatch):
    from tehm.batch_lane import import_support_to_canonical

    root = tmp_path / "campaign"
    observations = root / "external" / "observations.jsonl"
    write_external_observations(observations, [{
        "receipt_id": "known", "case_id": "known", "lineage_id": "lineage",
        "platform": "sky130hs", "family": "DENSITY_RELIEF", "split": "support",
        "classification": "INCOMPLETE_EXTERNAL_ONLY", "learner_eligible": False,
        "canonical_memory_mutation": "none", "promotion_eligible": False,
    }])
    staging = root / "staging" / "tehm.sqlite"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"staging-placeholder")
    monkeypatch.setattr(
        "tehm.batch_lane.validate_canonical_import_authority",
        lambda *args, **kwargs: None)
    with pytest.raises(BatchLaneError, match="unknown case_ids"):
        import_support_to_canonical(
            observations_path=observations, staging_db=staging,
            canonical_db=root / "canonical" / "tehm.sqlite",
            canonical_artifacts=root / "canonical" / "artifacts",
            campaign_id="batch0-unknown-case",
            authority={"case_ids": ["does-not-exist"]})


def test_rechaining_existing_receipts_discards_old_top_level_digest(tmp_path):
    path = tmp_path / "observations.jsonl"
    row = {
        "receipt_id": "r1", "case_id": "case", "lineage_id": "lineage",
        "platform": "sky130hs", "family": "DENSITY_RELIEF", "split": "support",
        "classification": "INCOMPLETE_EXTERNAL_ONLY", "learner_eligible": False,
        "canonical_memory_mutation": "none", "promotion_eligible": False,
    }
    write_external_observations(path, [row])
    first = read_external_observations(path)[0]["receipt_sha256"]
    write_external_observations(path, [{**row, "receipt_sha256": first}])
    second = read_external_observations(path)[0]["receipt_sha256"]
    assert second == read_external_observations(path)[0]["receipt_sha256"]
    assert second


def test_canonical_import_requires_all_gates_and_exact_bindings(tmp_path):
    observations = tmp_path / "observations.jsonl"
    observations.write_text("receipt\n")
    staging = tmp_path / "staging.sqlite"
    canonical = tmp_path / "canonical.sqlite"
    staging.write_bytes(b"staging")
    canonical.write_bytes(b"canonical")
    authority = {
        "version": "orfs-canonical-import-authority-v1",
        "decision": "ALLOW_CANONICAL_IMPORT",
        "promotion_gates": {},
        "bindings": {},
    }
    with pytest.raises(BatchLaneError, match="promotion gates"):
        validate_canonical_import_authority(
            authority, observations_path=observations,
            staging_db=staging, canonical_db=canonical)


def test_byte_identical_complete_rtl_set_is_positive_identity_proof(tmp_path):
    rtl = tmp_path / "top.v"
    rtl.write_text("module top(input clk); endmodule\n")
    result = YosysEquivalenceOracle(yosys="/definitely/missing").verify(
        reference_files=[rtl], candidate_files=[rtl],
        reference_top="top", candidate_top="top",
        reference_profile="flow.rtl.top-equivalence.v1",
        candidate_profile="flow.rtl.top-equivalence.v1")
    assert result["verdict"] == "PASS"
    assert result["proof_type"] == "CRYPTOGRAPHIC_SOURCE_IDENTITY_V1"


def test_orfs_toolchain_preflight_never_silently_uses_path_tools(tmp_path):
    from scripts.run_orfs_diversity_campaign import preflight_orfs_toolchain

    root = tmp_path / "orfs"
    (root / "flow").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n")
    result = preflight_orfs_toolchain(
        {"orfs_root": str(root)}, env={"PATH": "/usr/bin"})
    assert result["status"] == "blocked"
    assert "packaged openroad" in result["error"]
    assert "packaged yosys" in result["error"]


def test_orfs_toolchain_preflight_prefers_packaged_binaries(tmp_path):
    from scripts.run_orfs_diversity_campaign import preflight_orfs_toolchain

    root = tmp_path / "orfs"
    for path, version in (
            (root / "tools" / "install" / "OpenROAD" / "bin" / "openroad",
             "OpenROAD test"),
            (root / "tools" / "install" / "yosys" / "bin" / "yosys",
             "Yosys test")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\necho {version}\n")
        path.chmod(0o755)
    (root / "flow").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n")
    result = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    assert result["status"] == "bound_internal"
    assert result["tools"]["openroad"]["source"] == "orfs_packaged"
    assert result["tools"]["yosys"]["source"] == "orfs_packaged"
    assert result["environment"]["OPENROAD_EXE"].endswith("/openroad")


def test_orfs_toolchain_preflight_records_explicit_external_override(tmp_path):
    from scripts.run_orfs_diversity_campaign import preflight_orfs_toolchain

    root = tmp_path / "orfs"
    (root / "flow").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n")
    binaries = {}
    for name, switch, output in (("openroad", "-version", "OpenROAD ext"),
                                 ("yosys", "-V", "Yosys ext")):
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\necho {output}\n")
        path.chmod(0o755)
        binaries[name] = str(path)
    result = preflight_orfs_toolchain(
        {"orfs_root": str(root)},
        env={"OPENROAD_EXE": binaries["openroad"],
             "YOSYS_EXE": binaries["yosys"]})
    assert result["status"] == "bound_external"
    assert result["compatibility"] == "operator_bound_unverified"
    assert result["tools"]["openroad"]["path"] == binaries["openroad"]


def test_orfs_toolchain_preflight_rejects_real_yosys_without_required_option(
        tmp_path):
    from scripts.run_orfs_diversity_campaign import preflight_orfs_toolchain

    root = tmp_path / "orfs"
    (root / "flow" / "scripts").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n")
    (root / "flow" / "scripts" / "synth_canonicalize.tcl").write_text("")
    openroad = tmp_path / "openroad"
    openroad.write_text("#!/bin/sh\necho OpenROAD test\n")
    openroad.chmod(0o755)
    yosys = tmp_path / "yosys"
    yosys.write_text("#!/bin/sh\necho Yosys 0.9\n")
    yosys.chmod(0o755)

    result = preflight_orfs_toolchain(
        {"orfs_root": str(root)},
        env={"OPENROAD_EXE": str(openroad), "YOSYS_EXE": str(yosys)})
    assert result["status"] == "blocked"
    assert result["tools"]["yosys"]["capabilities"]["status"] == "FAIL"
    assert "read_liberty -unit_delay" in result["error"]


def test_orfs_toolchain_preflight_records_required_yosys_capability(tmp_path):
    from scripts.run_orfs_diversity_campaign import preflight_orfs_toolchain

    root = tmp_path / "orfs"
    (root / "flow" / "scripts").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n")
    (root / "flow" / "scripts" / "synth_canonicalize.tcl").write_text("")
    openroad = tmp_path / "openroad"
    openroad.write_text("#!/bin/sh\necho OpenROAD test\n")
    openroad.chmod(0o755)
    yosys = tmp_path / "yosys"
    yosys.write_text(
        "#!/bin/sh\necho Yosys 0.51\necho ' -unit_delay'\n")
    yosys.chmod(0o755)

    result = preflight_orfs_toolchain(
        {"orfs_root": str(root)},
        env={"OPENROAD_EXE": str(openroad), "YOSYS_EXE": str(yosys)})
    assert result["status"] == "bound_external"
    assert result["tools"]["yosys"]["capabilities"]["status"] == "PASS"
