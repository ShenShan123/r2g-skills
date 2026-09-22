from __future__ import annotations

import json
from pathlib import Path

import pytest

import tehm.evaluation.research_seed_pair as seed


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    epoch = tmp_path / "epoch"
    (epoch / "bindings").mkdir(parents=True)
    orfs = tmp_path / "orfs"
    orfs.mkdir()
    (epoch / "bindings/toolchain-manifest.json").write_text(json.dumps({
        "orfs": {"root": str(orfs), "git_head": "a" * 40},
        "pdk": {"root": str(tmp_path / "pdk")},
        "tools": {"openroad": {"path": "/tools/openroad"},
                  "yosys": {"path": "/tools/yosys"}},
    }), encoding="utf-8")
    script = tmp_path / "run_orfs.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cases = []
    provenance_entries = []
    for design, group in (("irq_ctrl", "owner:ultraembedded"),
                          ("sha256_core", "owner:secworks")):
        source = tmp_path / design / "source.v"
        source.parent.mkdir()
        source.write_text(f"module {design}; endmodule\n", encoding="utf-8")
        provenance_entries.append({
            "design_id": design, "source_group_conservative": group,
            "exact_file_match": True,
            "files": [{"local_file": str(source),
                       "sha256_upstream_and_local": seed._file_digest(source).removeprefix("sha256:")}],
        })
        arms = {}
        for arm in seed.ARMS:
            project = tmp_path / f"{design}-{arm}"
            project.mkdir()
            receipt = {
                "design_id": design, "platform": "sky130hs", "top_module": design,
                "receipt_digest": f"sha256:{design}-{arm}",
                "source_files": [{"source_path": "rtl/source.v", "sha256": seed._file_digest(source)}],
                "source_bundle_digest": "sha256:source-bundle",
                "config_template": {"sha256": "sha256:config"},
                "sdc_template": {"sha256": "sha256:sdc", "staged_sha256": "sha256:staged-sdc"},
                "declared_overrides": seed.EXPECTED_OVERRIDES[arm],
                "logic_changes": [], "stub_generated": False, "flow_executed": False,
            }
            (project / "stage-receipt.json").write_text(
                json.dumps(receipt), encoding="utf-8")
            arms[arm] = {"project": str(project),
                         "stage_receipt_digest": receipt["receipt_digest"]}
        cases.append({"design_id": design, "source_group": group, "arms": arms})
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"entries": provenance_entries}), encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "schema": seed.SCHEMA, "profile": "constructed_flow_feasibility_training",
        "epoch": str(epoch), "flow_script": str(script),
        "flow_script_sha256": seed._file_digest(script),
        "orfs_root": str(orfs), "orfs_git_head": "a" * 40,
        "provenance_file": str(provenance),
        "provenance_sha256": seed._file_digest(provenance),
        "pilot_development_source_groups": ["owner:other"],
        "stage_timeout_seconds": 900, "max_cpus": 2,
        "model_call_limit": 0, "retry_limit": 0, "cases": cases,
    }), encoding="utf-8")

    monkeypatch.setattr(seed, "verify_research_epoch", lambda _path: {
        "valid": True, "research_evaluation_ready": True,
        "epoch_digest": "sha256:epoch"})

    def checked(project: Path):
        receipt = seed._json(Path(project) / "stage-receipt.json")
        return {"valid": True, "design_id": receipt["design_id"],
                "receipt_digest": receipt["receipt_digest"]}

    monkeypatch.setattr(seed, "verify_staged_flow_project", checked)
    return spec


def _rewrite(path: Path, mutator) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutator(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_supplemental(spec: Path) -> None:
    payload = json.loads(spec.read_text(encoding="utf-8"))
    provenance = Path(payload["provenance_file"])
    provenance_payload = json.loads(provenance.read_text(encoding="utf-8"))
    provenance_payload["entries"] = provenance_payload["entries"][:1]
    provenance.write_text(json.dumps(provenance_payload), encoding="utf-8")
    payload["profile"] = "constructed_flow_feasibility_training_supplemental"
    payload["prior_seed_source_groups"] = ["owner:secworks"]
    payload["cases"] = payload["cases"][:1]
    payload["provenance_sha256"] = seed._file_digest(provenance)
    spec.write_text(json.dumps(payload), encoding="utf-8")


def test_seed_spec_requires_distinct_groups_and_single_registered_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _fixture(tmp_path, monkeypatch)
    assert seed.verify_seed_pair_spec(spec)["valid"] is True
    treatment = tmp_path / "irq_ctrl-treatment/stage-receipt.json"
    _rewrite(treatment, lambda data: data["declared_overrides"].update(
        {"CORE_UTILIZATION": "39"}))
    with pytest.raises(seed.ResearchSeedPairError, match="unregistered action"):
        seed.verify_seed_pair_spec(spec)
    _rewrite(treatment, lambda data: data.update(
        {"declared_overrides": seed.EXPECTED_OVERRIDES["treatment"]}))
    _rewrite(spec, lambda data: data["cases"][1].update(
        {"source_group": "owner:ultraembedded"}))
    with pytest.raises(seed.ResearchSeedPairError, match="source groups"):
        seed.verify_seed_pair_spec(spec)


def test_seed_spec_rejects_changed_clock_and_preexisting_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _fixture(tmp_path, monkeypatch)
    treatment = tmp_path / "irq_ctrl-treatment/stage-receipt.json"
    _rewrite(treatment, lambda data: data["sdc_template"].update(
        {"staged_sha256": "sha256:different"}))
    with pytest.raises(seed.ResearchSeedPairError, match="seed pair differs|clock constraints"):
        seed.verify_seed_pair_spec(spec)
    _rewrite(treatment, lambda data: data["sdc_template"].update(
        {"staged_sha256": "sha256:staged-sdc"}))
    prior = tmp_path / "irq_ctrl-control/backend/RUN_prior"
    prior.mkdir(parents=True)
    (prior / "run-meta.json").write_text("{}", encoding="utf-8")
    with pytest.raises(seed.ResearchSeedPairError, match="already executed"):
        seed.verify_seed_pair_spec(spec)


def test_supplemental_seed_accepts_one_new_group_and_rejects_prior_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _fixture(tmp_path, monkeypatch)
    _make_supplemental(spec)
    checked = seed.verify_seed_pair_spec(spec)
    assert checked["profile"] == (
        "constructed_flow_feasibility_training_supplemental")
    assert len(checked["cases"]) == 1
    _rewrite(spec, lambda data: data.update(
        {"prior_seed_source_groups": ["owner:ultraembedded"]}))
    with pytest.raises(seed.ResearchSeedPairError, match="overlaps prior seed"):
        seed.verify_seed_pair_spec(spec)


def test_seed_run_retains_four_arms_and_reverifies_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("ROUTE_FAST", "1")
    monkeypatch.setenv("R2G_ENV_FILE", "/unsafe/alternate-env")
    def execute(_script, project, _variant, env, log, _timeout):
        assert "ROUTE_FAST" not in env
        assert "R2G_ENV_FILE" not in env
        assert env["ORFS_TIMEOUT"] == "900"
        assert env["ORFS_MAX_CPUS"] == "2"
        log.write_text("bounded raw flow log\n", encoding="utf-8")
        (project / "backend/RUN_test").mkdir(parents=True)
        return 0, None

    def audit(*, project, output, **_kwargs):
        output.mkdir()
        arm = project.name.rsplit("-", 1)[1]
        verdict = "FAIL" if arm == "control" else "PASS"
        (output / "flow-audit.json").write_text(json.dumps({
            "project_path": str(project),
            "stage_receipt_digest": seed._json(project / "stage-receipt.json")["receipt_digest"],
            "run_dir": str(project / "backend/RUN_test"),
            "producer_epoch_digest": "sha256:epoch",
            "auditor_epoch_digest": "sha256:epoch",
        }), encoding="utf-8")
        return {"audit_digest": f"sha256:{project.name}",
                "oracle_verdict": verdict,
                "oracle_reason": "routing_congestion" if verdict == "FAIL" else "flow_completed",
                "failure_layer": "FLOW_TARGET_FAILURE" if verdict == "FAIL" else "NONE",
                "actual_cost": {"flow_driver_calls": 1,
                                "eda_stage_calls": 5 if verdict == "FAIL" else 6}}

    def verify(path):
        arm = Path(path).parent.name
        design = Path(path).parent.parent.name
        project_name = f"{design}-{arm}"
        verdict = "FAIL" if arm == "control" else "PASS"
        return {"audit_digest": f"sha256:{project_name}", "design_id": design,
                "oracle_verdict": verdict,
                "oracle_reason": "routing_congestion" if verdict == "FAIL" else "flow_completed",
                "failure_layer": "FLOW_TARGET_FAILURE" if verdict == "FAIL" else "NONE",
                "actual_cost": {"flow_driver_calls": 1,
                                "eda_stage_calls": 5 if verdict == "FAIL" else 6}}

    monkeypatch.setattr(seed, "_execute", execute)
    monkeypatch.setattr(seed, "audit_flow_run", audit)
    monkeypatch.setattr(seed, "verify_flow_audit", verify)
    output = tmp_path / "result"
    result = seed.run_seed_pair(spec=spec, output=output)
    assert result["valid"] is True
    assert result["all_registered_terminal"] is True
    assert result["two_source_group_positive_pairs"] is True
    assert result["positive_pair_count"] == 2
    assert result["all_registered_pairs_positive"] is True
    assert result["m0_status"] == "NOT_BUILT"
    assert len(result["outcomes"]) == 4
    assert result["actual_cost"]["audited_eda_stage_calls"] == 22
    assert result["actual_cost"]["audited_flow_driver_calls"] == 4
    assert seed.verify_seed_pair_run(output, spec)["valid"] is True
    raw_audit = output / "irq_ctrl/control/audit/flow-audit.json"
    original_audit = raw_audit.read_text(encoding="utf-8")
    _rewrite(raw_audit, lambda data: data.update(
        {"project_path": "/wrong/arm"}))
    with pytest.raises(seed.ResearchSeedPairError, match="different arm or epoch"):
        seed.verify_seed_pair_run(output, spec)
    raw_audit.write_text(original_audit, encoding="utf-8")
    ledger = output / "attempt-events.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2].replace("sha256:irq_ctrl-treatment", "sha256:tampered")
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(seed.ResearchSeedPairError, match="event chain"):
        seed.verify_seed_pair_run(output, spec)


def test_supplemental_seed_run_reports_only_its_registered_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _fixture(tmp_path, monkeypatch)
    _make_supplemental(spec)

    def execute(_script, project, _variant, _env, log, _timeout):
        log.write_text("bounded raw flow log\n", encoding="utf-8")
        (project / "backend/RUN_test").mkdir(parents=True)
        return 0, None

    def audit(*, project, output, **_kwargs):
        output.mkdir()
        arm = project.name.rsplit("-", 1)[1]
        verdict = "FAIL" if arm == "control" else "PASS"
        (output / "flow-audit.json").write_text(json.dumps({
            "project_path": str(project),
            "stage_receipt_digest": seed._json(
                project / "stage-receipt.json")["receipt_digest"],
            "run_dir": str(project / "backend/RUN_test"),
            "producer_epoch_digest": "sha256:epoch",
            "auditor_epoch_digest": "sha256:epoch",
        }), encoding="utf-8")
        return {"audit_digest": f"sha256:{project.name}",
                "oracle_verdict": verdict,
                "oracle_reason": "placement_density_infeasible"
                if verdict == "FAIL" else "flow_completed",
                "failure_layer": "FLOW_TARGET_FAILURE"
                if verdict == "FAIL" else "NONE",
                "actual_cost": {"flow_driver_calls": 1,
                                "eda_stage_calls": 3 if verdict == "FAIL" else 6}}

    def verify(path):
        arm = Path(path).parent.name
        design = Path(path).parent.parent.name
        verdict = "FAIL" if arm == "control" else "PASS"
        return {"audit_digest": f"sha256:{design}-{arm}", "design_id": design,
                "oracle_verdict": verdict,
                "oracle_reason": "placement_density_infeasible"
                if verdict == "FAIL" else "flow_completed",
                "failure_layer": "FLOW_TARGET_FAILURE"
                if verdict == "FAIL" else "NONE",
                "actual_cost": {"flow_driver_calls": 1,
                                "eda_stage_calls": 3 if verdict == "FAIL" else 6}}

    monkeypatch.setattr(seed, "_execute", execute)
    monkeypatch.setattr(seed, "audit_flow_run", audit)
    monkeypatch.setattr(seed, "verify_flow_audit", verify)
    result = seed.run_seed_pair(spec=spec, output=tmp_path / "supplemental")
    assert result["valid"] is True
    assert result["registered_pair_count"] == 1
    assert result["positive_pair_count"] == 1
    assert result["all_registered_pairs_positive"] is True
    assert "two_source_group_positive_pairs" not in result
    assert len(result["outcomes"]) == 2


def test_seed_run_keeps_unaudited_arm_unknown_in_full_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _fixture(tmp_path, monkeypatch)

    def execute(_script, project, _variant, _env, log, _timeout):
        log.write_text("raw attempt retained\n", encoding="utf-8")
        (project / "backend/RUN_test").mkdir(parents=True)
        return 2, None

    def audit(**_kwargs):
        raise RuntimeError("independent checker unavailable")

    monkeypatch.setattr(seed, "_execute", execute)
    monkeypatch.setattr(seed, "audit_flow_run", audit)
    result = seed.run_seed_pair(spec=spec, output=tmp_path / "inconclusive")
    assert result["valid"] is False
    assert result["all_registered_terminal"] is True
    assert len(result["outcomes"]) == 4
    assert {row["oracle_verdict"] for row in result["outcomes"]} == {"UNKNOWN"}
    assert result["two_source_group_positive_pairs"] is False
    assert result["memory_update"] == "none"
    assert result["actual_cost"]["unaudited_arm_count"] == 4
