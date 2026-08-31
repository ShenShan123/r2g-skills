from __future__ import annotations

import sys
import json
import hashlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_calibration_expansion import (  # noqa: E402
    LINEAGES,
    _external_transition_id,
    _grouped_shadow_admission,
    _grouped_shadow_readiness,
    _load_external_training,
    _persist_external_transition,
    _strict_oracle_gate,
    _strict_eligible_samples,
    _strict_oracle_one,
    _strict_oracle_projects,
    _contract_manifest_binding,
    _validate_contract_manifest,
    _validate_source_freeze,
    _contract_toolchain_gate,
    _contract_checks,
    _contract_equivalence_one,
    _contract_sample_gate,
    _strict_eligible_samples,
    make_samples,
    main,
    prepare,
    _subset_manifest,
)
from tehm import db as tehm_db
from tehm.artifact_store import ArtifactStore
from tehm import honesty
from tehm.sync import canonical_json
from tehm.physical.utility_contracts import (
    density_relief_nonregression_32,
    timing_relief_budgeted_v1,
)


def test_v87_v92_cohort_is_preregistered_and_source_disjoint():
    specs = {row["suffix"]: row for row in LINEAGES}
    suffixes = [f"v{index}" for index in range(87, 93)]
    assert [specs[suffix]["base"] for suffix in suffixes] == [
        "30", "32", "34", "30", "32", "34"]
    assert all(specs[suffix]["action"] == "40" for suffix in suffixes)

    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "physical_rtl"
    digests = set()
    for suffix in suffixes:
        design = specs[suffix]["design"]
        rtl = fixture_root / f"{design}.v"
        sdc = fixture_root / f"{design}.sdc"
        assert rtl.is_file() and sdc.is_file()
        assert f"module {design}" in rtl.read_text()
        assert f"current_design {design}" in sdc.read_text()
        digests.add(hashlib.sha256(rtl.read_bytes()).hexdigest())
    assert len(digests) == len(suffixes)


def test_v93_v104_action_screen_is_exact_and_preregistered():
    specs = {row["suffix"]: row for row in LINEAGES}
    action36 = [f"v{index}" for index in range(93, 99)]
    action38 = [f"v{index}" for index in range(99, 105)]
    assert [specs[suffix]["base"] for suffix in action36] == [
        "30", "32", "34", "30", "32", "34"]
    assert [specs[suffix]["base"] for suffix in action38] == [
        "30", "32", "34", "30", "32", "34"]
    assert {specs[suffix]["action"] for suffix in action36} == {"36"}
    assert {specs[suffix]["action"] for suffix in action38} == {"38"}
    for suffixes in (action36, action38):
        assert [specs[suffix]["screen_split"] for suffix in suffixes] == [
            "support", "support", "support",
            "heldout", "heldout", "heldout"]

    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "physical_rtl"
    rtl_digests = set()
    for suffix in action36 + action38:
        design = specs[suffix]["design"]
        rtl = fixture_root / f"{design}.v"
        sdc = fixture_root / f"{design}.sdc"
        assert rtl.is_file() and sdc.is_file()
        assert f"module {design}" in rtl.read_text()
        assert f"current_design {design}" in sdc.read_text()
        rtl_digests.add(hashlib.sha256(rtl.read_bytes()).hexdigest())
    assert len(rtl_digests) == 12

    replacements = {
        "v105": {"action": "36", "base": "34", "replacement_for": "v95"},
        "v106": {"action": "38", "base": "30", "replacement_for": "v99"},
    }
    for suffix, expected in replacements.items():
        replacement = specs[suffix]
        assert replacement["action"] == expected["action"]
        assert replacement["base"] == expected["base"]
        assert replacement["screen_split"] == "support"
        assert replacement["replacement_for"] == expected["replacement_for"]
        design = replacement["design"]
        rtl = fixture_root / f"{design}.v"
        sdc = fixture_root / f"{design}.sdc"
        assert rtl.is_file() and sdc.is_file()
        digest = hashlib.sha256(rtl.read_bytes()).hexdigest()
        assert digest not in rtl_digests
        rtl_digests.add(digest)
    assert len(rtl_digests) == 14


def test_v107_v112_action34_screen_avoids_noop_and_is_source_disjoint():
    specs = {row["suffix"]: row for row in LINEAGES}
    suffixes = [f"v{index}" for index in range(107, 113)]
    assert [specs[suffix]["base"] for suffix in suffixes] == [
        "28", "30", "32", "28", "30", "32"]
    assert {specs[suffix]["action"] for suffix in suffixes} == {"34"}
    assert all(specs[suffix]["base"] != specs[suffix]["action"]
               for suffix in suffixes)
    assert [specs[suffix]["screen_split"] for suffix in suffixes] == [
        "support", "support", "support", "heldout", "heldout", "heldout"]

    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "physical_rtl"
    prior_digests = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in fixture_root.glob("future_prospective_logic_v*.v")
        if path.stem not in {specs[suffix]["design"] for suffix in suffixes}
    }
    new_digests = set()
    for suffix in suffixes:
        design = specs[suffix]["design"]
        rtl = fixture_root / f"{design}.v"
        sdc = fixture_root / f"{design}.sdc"
        assert rtl.is_file() and sdc.is_file()
        digest = hashlib.sha256(rtl.read_bytes()).hexdigest()
        assert digest not in prior_digests
        new_digests.add(digest)
    assert len(new_digests) == 6


def test_v113_v118_action32_screen_avoids_noop_and_is_source_disjoint():
    specs = {row["suffix"]: row for row in LINEAGES}
    suffixes = [f"v{index}" for index in range(113, 119)]
    assert [specs[suffix]["base"] for suffix in suffixes] == [
        "24", "26", "28", "24", "26", "28"]
    assert {specs[suffix]["action"] for suffix in suffixes} == {"32"}
    assert all(specs[suffix]["base"] != specs[suffix]["action"]
               for suffix in suffixes)
    assert [specs[suffix]["screen_split"] for suffix in suffixes] == [
        "support", "support", "support", "heldout", "heldout", "heldout"]

    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "physical_rtl"
    new_designs = {specs[suffix]["design"] for suffix in suffixes}
    prior_digests = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in fixture_root.glob("future_prospective_logic_v*.v")
        if path.stem not in new_designs
    }
    new_digests = set()
    for suffix in suffixes:
        design = specs[suffix]["design"]
        rtl = fixture_root / f"{design}.v"
        sdc = fixture_root / f"{design}.sdc"
        assert rtl.is_file() and sdc.is_file()
        digest = hashlib.sha256(rtl.read_bytes()).hexdigest()
        assert digest not in prior_digests
        new_digests.add(digest)
    assert len(new_digests) == 6


def test_v119_v121_are_reserved_for_future_shadow_observation():
    specs = {row["suffix"]: row for row in LINEAGES}
    suffixes = ["v119", "v120", "v121"]
    assert [specs[suffix]["base"] for suffix in suffixes] == ["24", "26", "28"]
    assert {specs[suffix]["action"] for suffix in suffixes} == {"32"}
    assert all(specs[suffix]["base"] != specs[suffix]["action"] for suffix in suffixes)
    assert {specs[suffix]["screen_split"] for suffix in suffixes} == {"future_observation"}
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "physical_rtl"
    prior = {hashlib.sha256(path.read_bytes()).hexdigest()
             for path in fixture_root.glob("future_prospective_logic_v*.v")
             if path.stem not in {specs[suffix]["design"] for suffix in suffixes}}
    digests = set()
    for suffix in suffixes:
        design = specs[suffix]["design"]
        rtl, sdc = fixture_root / f"{design}.v", fixture_root / f"{design}.sdc"
        assert rtl.is_file() and sdc.is_file()
        assert f"module {design}" in rtl.read_text()
        assert f"current_design {design}" in sdc.read_text()
        digest = hashlib.sha256(rtl.read_bytes()).hexdigest()
        assert digest not in prior
        digests.add(digest)
    assert len(digests) == 3


def test_v122_v127_are_a_new_contract_bound_source_disjoint_cohort():
    specs = {row["suffix"]: row for row in LINEAGES}
    support = [f"v{index}" for index in range(122, 125)]
    heldout = [f"v{index}" for index in range(125, 128)]
    assert [specs[suffix]["base"] for suffix in support + heldout] == [
        "24", "26", "28", "24", "26", "28"]
    assert {specs[suffix]["action"] for suffix in support + heldout} == {"32"}
    assert {specs[suffix]["screen_split"] for suffix in support} == {
        "contract_support"}
    assert {specs[suffix]["screen_split"] for suffix in heldout} == {
        "contract_heldout"}
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "physical_rtl"
    prior = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in fixture_root.glob("future_prospective_logic_v*.v")
        if path.stem not in {specs[suffix]["design"] for suffix in support + heldout}
    }
    digests = set()
    for suffix in support + heldout:
        design = specs[suffix]["design"]
        rtl = fixture_root / f"{design}.v"
        sdc = fixture_root / f"{design}.sdc"
        assert rtl.is_file() and sdc.is_file()
        assert f"module {design}" in rtl.read_text()
        assert f"current_design {design}" in sdc.read_text()
        digest = hashlib.sha256(rtl.read_bytes()).hexdigest()
        assert digest not in prior
        digests.add(digest)
    assert len(digests) == 6


def test_v128_v133_are_preregistered_as_a_second_contract_cohort():
    specs = {row["suffix"]: row for row in LINEAGES}
    suffixes = [f"v{index}" for index in range(128, 134)]
    assert [specs[suffix]["base"] for suffix in suffixes] == ["24"] * 6
    assert {specs[suffix]["action"] for suffix in suffixes} == {"32"}
    assert {specs[suffix]["screen_split"] for suffix in suffixes[:3]} == {
        "contract_support_v2"}
    assert {specs[suffix]["screen_split"] for suffix in suffixes[3:]} == {
        "contract_heldout_v2"}
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "physical_rtl"
    prior = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in fixture_root.glob("future_prospective_logic_v*.v")
        if path.stem not in {specs[suffix]["design"] for suffix in suffixes}
    }
    digests = set()
    for suffix in suffixes:
        design = specs[suffix]["design"]
        rtl = fixture_root / f"{design}.v"
        sdc = fixture_root / f"{design}.sdc"
        assert rtl.is_file() and sdc.is_file()
        assert f"module {design}" in rtl.read_text()
        assert f"current_design {design}" in sdc.read_text()
        digest = hashlib.sha256(rtl.read_bytes()).hexdigest()
        assert digest not in prior
        digests.add(digest)
    assert len(digests) == 6


def test_contract_equivalence_receipt_is_independent_and_source_bound(tmp_path):
    rtl = tmp_path / "design.v"
    rtl.write_text("module design(input wire a, output wire y); assign y = a; endmodule\n")
    project = tmp_path / "project"
    result = _contract_equivalence_one(
        project,
        {"case_id": "case-a", "design": "design", "rtl_files": [str(rtl)]},
        timeout=5,
    )
    assert result["verdict"] == "PASS"
    assert result["proof_type"] == "CRYPTOGRAPHIC_SOURCE_IDENTITY_V1"
    receipt = json.loads((project / "reports" / "equivalence.json").read_text())
    assert receipt["oracle_type"] == "YOSYS_EQUIV"
    assert receipt["case_id"] == "case-a"


def test_contract_sample_gate_does_not_treat_generic_safe_as_contract_safe():
    samples = [
        {"case_id": "case-pass", "lineage_id": "l-pass",
         "contract_evaluation": {"status": "PASS"}},
        {"case_id": "case-fail", "lineage_id": "l-fail",
         "contract_evaluation": {"status": "FAIL", "failures": ["power_budget_exceeded"]}},
        {"case_id": "case-missing", "lineage_id": "l-missing"},
    ]
    gate = _contract_sample_gate(samples)
    assert gate["status"] == "FAIL"
    assert gate["pass_count"] == 1
    assert gate["fail_count"] == 1
    assert gate["abstain_count"] == 1


def test_grouped_shadow_readiness_is_shadow_only_and_evidence_derived():
    grouped = {
        "status": "ready_for_shadow",
        "groups": {"group": {"checks": {"conformal_lineage_coverage": True}}},
    }
    materialized = {"status": "materialized_shadow_only", "policy": {
        "status": "ready", "shadow_only": True, "promotion_eligible": False,
        "canonical_memory_mutation": "none", "scope": {"platform": "sky130hs",
        "family": "DENSITY_RELIEF", "dataset_tier": "strict_clean"},
        "policy_kind": "lineage_grouped_shadow", "firewall": {
            "heldout_lineages": ["a", "b", "c"]},
        "thresholds": {"max_distance": 0.6},
        "calibration": {"empirical_coverage": 1.0, "required_coverage": 0.8,
                         "conformal_quantiles": {"area_um2": 1.0}},
    }}
    readiness = _grouped_shadow_readiness(grouped_report=grouped,
                                           materialization=materialized)
    assert readiness["status"] == "READY_FOR_IMPLEMENTATION"
    assert readiness["parametric_view_status"] == "NOT_IMPLEMENTED"
    assert readiness["shadow_only"] is True
    assert readiness["promotion_eligible"] is False


def test_grouped_shadow_admission_rejects_harmful_heldout_lineage():
    action = {"domain": "flow.CONFIG_DELTA",
              "transformation_family": "DENSITY_RELIEF",
              "payload": {"config_edits": {"CORE_UTILIZATION": "40"}}}
    samples = []
    evaluations = []
    for index in range(4):
        wns = -0.08 if index == 0 else 0.01
        samples.append({
            "case_id": f"case-{index}", "lineage_id": f"heldout:{index}",
            "platform": "sky130hs", "family": "DENSITY_RELIEF",
            "expected_tier": "strict_clean", "action": action,
            "graph_context": {"platform": "sky130hs",
                              "dataset_tier": "strict_clean"},
            "observed_deltas": {"wns_ns": wns, "area_um2": -1.0},
        })
        evaluations.append({
            "index": index, "status": "evaluated",
            "metrics": {
                "wns_ns": {"predicted": 0.0},
                "area_um2": {"predicted": -0.5},
            },
        })
    report, materialization = _grouped_shadow_admission(
        retrieval_policy={"evaluations": evaluations,
                          "thresholds": {"max_distance": 0.5}},
        heldout_samples=samples, training_lineages=["training:a"])
    assert report["status"] == "shadow_calibration_failed"
    group = next(iter(report["groups"].values()))
    assert group["checks"]["harmful_rate"] is False
    assert group["safety"]["harmful_rate"] == 0.25
    assert materialization == {
        "status": "not_materialized",
        "reason": "grouped_calibration_not_ready",
        "policy": None,
    }


def test_grouped_shadow_admission_materializes_only_safe_shadow_policy():
    action = {"domain": "flow.CONFIG_DELTA",
              "transformation_family": "DENSITY_RELIEF",
              "payload": {"config_edits": {"CORE_UTILIZATION": "40"}}}
    samples = [{
        "case_id": f"case-{index}", "lineage_id": f"heldout:{index}",
        "platform": "sky130hs", "family": "DENSITY_RELIEF",
        "expected_tier": "strict_clean", "action": action,
        "graph_context": {"platform": "sky130hs",
                          "dataset_tier": "strict_clean"},
        "observed_deltas": {"wns_ns": 0.01, "area_um2": -1.0},
    } for index in range(3)]
    evaluations = [{
        "index": index, "status": "evaluated",
        "metrics": {"wns_ns": {"predicted": 0.0},
                    "area_um2": {"predicted": -0.5}},
    } for index in range(3)]
    report, materialization = _grouped_shadow_admission(
        retrieval_policy={"evaluations": evaluations,
                          "thresholds": {"max_distance": 0.5}},
        heldout_samples=samples, training_lineages=["training:a"])
    assert report["status"] == "ready_for_shadow"
    assert materialization["status"] == "materialized_shadow_only"
    policy = materialization["policy"]
    assert policy["policy_kind"] == "lineage_grouped_shadow"
    assert policy["shadow_only"] is True
    assert policy["promotion_eligible"] is False
    assert policy["canonical_memory_mutation"] == "none"


def test_subset_manifest_selects_scratch_lineages_without_mutating_source():
    manifest = {
        "items": [
            {"case_id": "v39", "lineage_id": "future-prospective-v39:sky130hs:x"},
            {"case_id": "v40", "lineage_id": "future-prospective-v40:sky130hs:x"},
        ],
        "version": "calibration-expansion-v1",
    }
    selected = _subset_manifest(manifest, {"v40"})
    assert [item["case_id"] for item in selected["items"]] == ["v40"]
    assert selected["selected_suffixes"] == ["v40"]
    assert len(manifest["items"]) == 2


def test_contract_manifest_is_bound_before_evaluation():
    contract = density_relief_nonregression_32()
    frozen = _contract_manifest_binding(contract)
    resolved = _validate_contract_manifest({"utility_contract": frozen})
    assert resolved["contract_id"] == contract["contract_id"]

    with pytest.raises(ValueError, match="pre-registered during prepare"):
        _validate_contract_manifest({}, contract)
    with pytest.raises(ValueError, match="requested utility contract differs"):
        _validate_contract_manifest(
            {"utility_contract": frozen}, timing_relief_budgeted_v1())

    tampered = dict(frozen)
    tampered["contract_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest/signature mismatch"):
        _validate_contract_manifest({"utility_contract": tampered})


def test_prepare_binds_contract_to_selected_cohort_before_flow(tmp_path):
    orfs = tmp_path / "orfs"
    template = orfs / "flow" / "designs" / "sky130hs" / "gcd"
    template.mkdir(parents=True)
    (template / "config.mk").write_text(
        "DESIGN_NAME = gcd\nCORE_UTILIZATION = 50\n")
    (template / "constraint.sdc").write_text("current_design gcd\n")
    contract = density_relief_nonregression_32()
    manifest = prepare(
        tmp_path / "campaign", orfs,
        suffixes={"v113", "v114", "v115"}, utility_contract=contract)
    assert manifest["utility_contract"]["binding"] == "PREPARE_TIME"
    assert manifest["utility_contract"]["contract_id"] == contract["contract_id"]
    assert manifest["source_freeze_digest"]
    assert _validate_source_freeze(manifest)["version"] == \
        "calibration-expansion-source-freeze-v1"
    assert {item["utility_contract_id"] for item in manifest["items"]} == {
        contract["contract_id"]}
    assert {item["config_edits"]["CORE_UTILIZATION"]
            for item in manifest["items"]} == {"32"}

    config = Path(manifest["items"][0]["after_project"]) / "constraints" / "config.mk"
    config.write_text(config.read_text() + "# drift\n")
    with pytest.raises(ValueError, match="materialized inputs changed"):
        _validate_source_freeze(manifest)

    with pytest.raises(ValueError, match="not bound to utility contract"):
        prepare(tmp_path / "mixed", orfs,
                suffixes={"v112", "v113"}, utility_contract=contract)


def test_make_samples_rejects_item_contract_binding_drift(tmp_path):
    contract = density_relief_nonregression_32()
    manifest = {
        "utility_contract": _contract_manifest_binding(contract),
        "items": [{
            "case_id": "case-a", "lineage_id": "lineage-a", "platform": "sky130hs",
            "family": "DENSITY_RELIEF", "before_project": str(tmp_path / "before"),
            "after_project": str(tmp_path / "after"),
            "config_edits": {"CORE_UTILIZATION": "32"},
            "utility_contract_id": "WRONG",
        }],
    }
    with pytest.raises(ValueError, match="item utility contract binding differs"):
        make_samples(tmp_path, manifest)


def test_contract_cohort_rejects_unverified_toolchain_before_sampling(tmp_path):
    contract = density_relief_nonregression_32()
    manifest = {"utility_contract": _contract_manifest_binding(contract)}
    assert _contract_toolchain_gate(tmp_path, manifest)["eligible"] is False
    (tmp_path / "toolchain_preflight.json").write_text(json.dumps({
        "status": "bound_external", "fingerprint": "external",
        "compatibility": "operator_bound_unverified",
    }))
    gate = _contract_toolchain_gate(tmp_path, manifest)
    assert gate["eligible"] is False
    assert "bound_internal" in gate["reason"]

    samples_path = tmp_path / "samples.json"
    samples_path.write_text(json.dumps({
        "samples": [{"case_id": "case-a"}],
        "evidence": [{
            "case_id": "case-a", "status": "evaluatable",
            "strict_oracle": {"eligible": True},
            "toolchain_preflight": gate,
        }],
    }))
    accepted, excluded = _strict_eligible_samples(
        samples_path, require_internal_toolchain=True)
    assert accepted == []
    assert excluded[0]["reason"].startswith("toolchain_preflight_unverified")

    legacy = _contract_toolchain_gate(tmp_path, {})
    assert legacy["eligible"] is True


def test_promote_phase_accepts_sample_only_support_campaign(tmp_path):
    root = tmp_path / "scratch"
    evidence = tmp_path / "evidence"
    root.mkdir()
    (root / "campaign_manifest.json").write_text(json.dumps({
        "version": "calibration-expansion-v1", "items": [],
    }))
    (root / "prospective_samples.json").write_text(json.dumps({
        "samples": [{"lineage_id": "support:a"}], "evidence": [],
    }))
    missing = tmp_path / "not-used.json"
    assert main([
        "--phase", "promote", "--root", str(root),
        "--evidence-root", str(evidence),
        "--v10v11-samples", str(missing),
        "--v12v13-pairs", str(missing),
        "--training-manifest", str(missing),
    ]) == 0
    assert not (root / "calibration_expansion_report.json").exists()
    promoted = json.loads((evidence / "promotion_report.json").read_text())
    assert promoted["evaluated_lineages"] == ["support:a"]
    assert promoted["promotion_eligible"] is False


def test_strict_oracle_runs_signoff_and_timing_and_reuses_bound_receipt(tmp_path,
                                                                        monkeypatch):
    project = tmp_path / "project"
    run = project / "backend" / "RUN_demo"
    run.mkdir(parents=True)
    (run / "run-meta.json").write_text(json.dumps({"run_tag": "RUN_demo"}))
    manifest = {"items": [{"platform": "sky130hs",
                            "before_project": str(project),
                            "after_project": str(project)}]}
    assert _strict_oracle_projects(manifest) == [(project, "sky130hs")]
    calls = []

    class Proc:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        reports = project / "reports"
        if cmd[1].endswith("run_strict_signoff.sh"):
            (reports / "strict_signoff.json").write_text(json.dumps({
                "run_tag": "RUN_demo", "status": "pass"}))
        else:
            (reports / "timing_check.json").write_text(json.dumps({
                "status": "clean", "tier": "clean"}))
        return Proc()

    monkeypatch.setattr("run_calibration_expansion.subprocess.run", fake_run)
    first = _strict_oracle_one(project, "sky130hs", timeout=10)
    assert first["strict_rc"] == 0
    assert first["timing_rc"] == 0
    assert first["strict_status"] == "pass"
    assert first["timing_status"] == "clean"
    assert len(calls) == 2

    second = _strict_oracle_one(project, "sky130hs", timeout=10)
    assert second["reused"] is True
    assert second["strict_rc"] is None
    assert second["timing_rc"] is None
    assert len(calls) == 2


def test_strict_oracle_gate_excludes_dirty_or_missing_projects(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    manifest = {
        "items": [{"case_id": "case-a", "before_project": str(before),
                   "after_project": str(after)}]
    }
    (tmp_path / "strict_oracle_state.json").write_text(json.dumps({
        "version": "calibration-strict-oracle-v1", "requested": True,
        "projects": [
            {"project": str(before), "run_tag": "RUN_before",
             "strict_report_run_tag": "RUN_before", "strict_status": "fail",
             "timing_status": "clean", "strict_rc": 0, "timing_rc": 0},
            {"project": str(after), "run_tag": "RUN_after",
             "strict_report_run_tag": "RUN_after", "strict_status": "pass",
             "timing_status": "clean", "strict_rc": 0, "timing_rc": 0},
        ],
    }))
    gate = _strict_oracle_gate(tmp_path, manifest)
    assert gate["case-a"]["eligible"] is False
    assert "before_project:strict_status=fail" in gate["case-a"]["reason"]

    (tmp_path / "strict_oracle_state.json").write_text(json.dumps({
        "version": "calibration-strict-oracle-v1", "requested": True,
        "projects": [
            {"project": str(before), "run_tag": "RUN_before",
             "strict_report_run_tag": "RUN_before", "strict_status": "pass",
             "timing_status": "clean", "strict_rc": 0, "timing_rc": 0},
            {"project": str(after), "run_tag": "RUN_after",
             "strict_report_run_tag": "RUN_after", "strict_status": "pass",
             "timing_status": "clean", "strict_rc": 0, "timing_rc": 0},
        ],
    }))
    assert _strict_oracle_gate(tmp_path, manifest)["case-a"]["eligible"] is True

    (tmp_path / "strict_oracle_state.json").unlink()
    missing = _strict_oracle_gate(tmp_path, manifest)
    assert missing["case-a"]["reason"] == "strict_oracle_missing"


def test_make_samples_never_calibrates_without_strict_pass(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    manifest = {
        "items": [{"case_id": "case-a", "lineage_id": "lineage-a",
                   "platform": "sky130hs", "family": "DENSITY_RELIEF",
                   "before_project": str(before), "after_project": str(after),
                   "config_edits": {"CORE_UTILIZATION": "40"}}]
    }
    result = make_samples(tmp_path, manifest)
    assert result["samples"] == []
    assert result["evidence"][0]["status"] == "excluded_strict_oracle"
    assert result["evidence"][0]["strict_oracle"]["reason"] == \
        "strict_oracle_missing"


def test_strict_eligible_sample_loader_rejects_legacy_rows(tmp_path):
    path = tmp_path / "samples.json"
    sample = {"case_id": "case-a", "lineage_id": "lineage-a"}
    path.write_text(json.dumps({
        "samples": [sample, {"case_id": "case-b", "lineage_id": "lineage-b"}],
        "evidence": [{
            "case_id": "case-a", "status": "evaluatable",
            "strict_oracle": {"eligible": True},
        }],
    }))
    accepted, excluded = _strict_eligible_samples(path)
    assert accepted == [sample]
    assert excluded == [{"case_id": "case-b",
                         "reason": "strict_oracle_evidence_missing"}]

def test_external_calibration_transition_is_immutable(tmp_path):
    conn = tehm_db.connect(tmp_path / "staging.sqlite")
    tehm_db.ensure_schema(conn)
    sample = {"case_id": "case-a", "lineage_id": "lineage-a",
              "graph_context": {"digest": "graph-a"}}
    action = {"domain": "flow.CONFIG_DELTA",
              "transformation_family": "DENSITY_RELIEF",
              "payload": {"config_edits": {"CORE_UTILIZATION": "40"}}}
    transition_id = _external_transition_id({**sample, "action": action})
    action_json = canonical_json(action).decode()
    _persist_external_transition(
        conn, transition_id=transition_id, sample=sample,
        action=action, action_json=action_json)
    _persist_external_transition(
        conn, transition_id=transition_id, sample=sample,
        action=action, action_json=action_json)
    conn.execute("UPDATE tehm_transitions SET action_json=? WHERE transition_id=?",
                 ("{}", transition_id))
    conn.commit()
    with pytest.raises(ValueError, match="immutable and conflicts"):
        _persist_external_transition(
            conn, transition_id=transition_id, sample=sample,
            action=action, action_json=action_json)
    conn.close()


def test_external_training_staging_is_atomic_on_late_failure(tmp_path):
    conn = tehm_db.connect(tmp_path / "staging.sqlite")
    tehm_db.ensure_schema(conn)
    action = {"domain": "flow.CONFIG_DELTA",
              "transformation_family": "DENSITY_RELIEF",
              "payload": {"config_edits": {"CORE_UTILIZATION": "40"}}}
    valid = {"case_id": "case-a", "lineage_id": "lineage-a",
             "graph_context": {}, "action": action,
             "before_ppa": {}, "after_ppa": {}}
    malformed = {"case_id": "case-b", "lineage_id": "lineage-b",
                 "graph_context": {}, "action": action}
    with pytest.raises(KeyError, match="before_ppa"):
        _load_external_training(
            tmp_path, conn, ArtifactStore(tmp_path / "artifacts"),
            [valid, malformed])
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tehm_states").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tehm_physical_effects").fetchone()[0] == 0
    conn.close()


def test_external_training_staging_binds_states_and_audit_membership(tmp_path):
    conn = tehm_db.connect(tmp_path / "staging.sqlite")
    tehm_db.ensure_schema(conn)
    action = {"domain": "flow.CONFIG_DELTA",
              "transformation_family": "DENSITY_RELIEF",
              "payload": {"config_edits": {"CORE_UTILIZATION": "40"}}}
    sample = {
        "case_id": "case-a", "lineage_id": "lineage-a", "graph_context": {},
        "action": action,
        "before_ppa": {"summary": {"area": {"design_area_um2": 10.0}}},
        "after_ppa": {"summary": {"area": {"design_area_um2": 9.0}},
                      },
    }
    _load_external_training(
        tmp_path, conn, ArtifactStore(tmp_path / "artifacts"), [sample])
    assert conn.execute("SELECT COUNT(*) FROM tehm_states").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tehm_dataset_membership").fetchone()[0] == 1
    membership = conn.execute(
        "SELECT split, learner_eligible FROM tehm_dataset_membership").fetchone()
    assert membership["split"] == "calibration"
    assert membership["learner_eligible"] == 0
    ok, detail = honesty.h1_transition_completeness(conn)
    assert ok, detail
    conn.close()
