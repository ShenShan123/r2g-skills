from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from pathlib import Path

import pytest

from tehm.adapters import research_seed_scoped as scoped
from tehm.canonical.capture import capture
from tehm.causal.intervention import build_intervention_pair
from tehm.dataset import assign_transition
from tehm.ids import stable_dumps
from tehm.evaluation import research_seed_m0 as seed_m0
from tehm.sync import verify_bundle
from tehm.verified_execution import require_verified_transition, scoped_learning_replay


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    design: str = "seed",
    lineage: str = "source-group:seed",
    source_group: str = "owner:seed",
) -> dict:
    projects = []
    audits = []
    source_bytes = f"module {design}; endmodule\n".encode()
    sdc_bytes = b"create_clock -period 1.4 [get_ports clk]\n"
    for arm, density, verdict, reason, layer in (
        ("control", "95", "FAIL", "placement_density_infeasible",
         "FLOW_TARGET_FAILURE"),
        ("treatment", "40", "PASS", "flow_completed", "NONE"),
    ):
        project = tmp_path / arm
        (project / "constraints").mkdir(parents=True)
        (project / "rtl").mkdir()
        include_dir = project / "rtl/include"
        include_dir.mkdir()
        (include_dir / "seed_defs.vh").write_text(
            "`define SEED_WIDTH 8\n", encoding="utf-8")
        source = project / f"rtl/{design}.v"
        sdc = project / "constraints/constraint.sdc"
        source.write_bytes(source_bytes)
        sdc.write_bytes(sdc_bytes)
        (project / "constraints/config.mk").write_text(
            "\n".join((
                f"export DESIGN_NAME = {design}",
                "export PLATFORM = sky130hs",
                f"export VERILOG_FILES = {source}",
                f"export VERILOG_INCLUDE_DIRS = {include_dir}",
                f"export SDC_FILE = {sdc}",
                f"export CORE_UTILIZATION = {density}",
            )) + "\n", encoding="utf-8")
        receipt = {
            "receipt_digest": f"sha256:{design}-{arm}", "design_id": design,
            "platform": "sky130hs", "top_module": design,
            "source_files": [{"source_path": f"rtl/{design}.v",
                              "staged_path": f"rtl/{design}.v",
                              "bytes": len(source_bytes),
                              "sha256": "sha256:" + hashlib.sha256(
                                  source_bytes).hexdigest()}],
            "source_bundle_digest": f"sha256:bundle-{design}",
            "sdc_template": {"staged_sha256": "sha256:" + hashlib.sha256(
                sdc_bytes).hexdigest()},
            "declared_overrides": {"CORE_UTILIZATION": density},
            "logic_changes": [], "stub_generated": False,
        }
        (project / "stage-receipt.json").write_text(
            json.dumps(receipt), encoding="utf-8")
        audit_dir = tmp_path / f"{arm}-audit"
        audit_dir.mkdir()
        raw_file = audit_dir / "raw.log"
        raw_file.write_text(f"{arm} raw\n", encoding="utf-8")
        audit = {
            "audit_digest": f"sha256:audit-{design}-{arm}",
            "project_path": str(project),
            "source_mutation": "none", "memory_update": "none",
            "production_authority": False, "oracle_verdict": verdict,
            "oracle_reason": reason, "failure_layer": layer,
            "design_id": design, "stage_receipt_digest": receipt["receipt_digest"],
            "toolchain_manifest_digest": "toolchain-digest",
            "run_tag": f"RUN_{design}_{arm}",
            "raw_artifacts": [{"path": str(raw_file), "bytes": raw_file.stat().st_size,
                               "sha256": scoped._file_digest(raw_file)}],
        }
        (audit_dir / "flow-audit.json").write_text(
            json.dumps(audit), encoding="utf-8")
        (audit_dir / "artifact-manifest.json").write_text(
            json.dumps({"arm": arm}), encoding="utf-8")
        projects.append(project)
        audits.append(audit_dir)

    def verify_project(project):
        receipt = scoped._json(Path(project) / "stage-receipt.json")
        return {"valid": True, "receipt_digest": receipt["receipt_digest"]}

    def verify_audit(path):
        audit = scoped._json(Path(path) / "flow-audit.json")
        return {"valid": True, "audit_digest": audit["audit_digest"]}

    monkeypatch.setattr(scoped, "verify_staged_flow_project", verify_project)
    monkeypatch.setattr(scoped, "verify_flow_audit", verify_audit)
    return {
        "version": scoped.ACQUISITION_VERSION,
        "before_project": str(projects[0]), "after_project": str(projects[1]),
        "before_audit": str(audits[0]), "after_audit": str(audits[1]),
        "lineage_id": lineage, "source_group": source_group,
        "config_edits": {"CORE_UTILIZATION": "40"},
        "expected_toolchain_manifest_digest": "toolchain-digest",
    }


def test_research_seed_record_replays_both_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition = _fixture(tmp_path, monkeypatch)
    treatment = scoped.build_research_seed_record(acquisition)
    control = scoped.build_research_seed_record({**acquisition, "role": "control"})
    assert treatment.verification["verdict"] == "PASS"
    assert treatment.observation_delta["original_failure"] == "REMOVED"
    assert control.verification["verdict"] == "FAIL"
    assert control.before == control.after
    assert any("control-audit" in item for item in control.verification["evidence_refs"])
    assert not any("treatment-audit" in item for item in control.verification["evidence_refs"])
    assert scoped.replay_research_seed_record(treatment)[
        "controlled_measurement_valid"] is True
    assert scoped.replay_research_seed_record(control)[
        "controlled_measurement_valid"] is True
    changed = json.loads(json.dumps(acquisition))
    changed["config_edits"]["CORE_UTILIZATION"] = "41"
    with pytest.raises(scoped.ResearchSeedScopedError, match="frozen density relief"):
        scoped.verify_research_seed_acquisition(changed)


def test_research_seed_persisted_replay_enables_only_scoped_training(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_tehm,
) -> None:
    acquisition = _fixture(tmp_path, monkeypatch)
    conn, store, _ = tmp_tehm
    treatment = capture(
        conn, store, scoped.build_research_seed_record(acquisition),
        dataset_campaign_id="seed-ingress-diagnostic", dataset_learner_eligible=False)
    control_acquisition = {**acquisition, "role": "control"}
    control = capture(
        conn, store, scoped.build_research_seed_record(control_acquisition),
        dataset_campaign_id="seed-ingress-diagnostic", dataset_learner_eligible=False)
    acquisitions = {
        treatment.transition_id: acquisition,
        control.transition_id: control_acquisition,
    }
    digest = "sha256:" + hashlib.sha256(
        stable_dumps(acquisitions).encode()).hexdigest()
    conn.commit()
    ram = sqlite3.connect(":memory:")
    ram.row_factory = sqlite3.Row
    conn.backup(ram)
    try:
        with pytest.raises(ValueError, match="explicit_training_membership"):
            with scoped_learning_replay(
                    ram, campaign_id="seed-training", acquisitions=acquisitions,
                    expected_digest=digest):
                require_verified_transition(ram, treatment.transition_id)
        for transition_id in acquisitions:
            assign_transition(ram, transition_id=transition_id,
                              campaign_id="seed-training", learner_eligible=True)
        with scoped_learning_replay(
                ram, campaign_id="seed-training", acquisitions=acquisitions,
                expected_digest=digest):
            require_verified_transition(ram, treatment.transition_id)
            require_verified_transition(ram, control.transition_id)
            pair = build_intervention_pair(
                ram, control.transition_id, treatment.transition_id,
                campaign_id="seed-training", target_scope="flow_feasibility")
            assert pair.validity_status == "VALID_CONTROLLED_PAIR"
    finally:
        ram.close()


def test_build_research_seed_m0_exports_consumable_read_only_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _fixture(
        tmp_path / "first", monkeypatch, design="seed_a",
        lineage="owner-a:seed-a", source_group="owner:a")
    second = _fixture(
        tmp_path / "second", monkeypatch, design="seed_b",
        lineage="owner-b:seed-b", source_group="owner:b")
    epoch = tmp_path / "epoch"
    epoch.mkdir()
    (epoch / "research-epoch.json").write_text("{}\n", encoding="utf-8")
    (epoch / "artifact-manifest.json").write_text("{}\n", encoding="utf-8")
    checked_epoch = {
        "valid": True, "epoch_id": "clean-seed-builder", "status": "FROZEN",
        "research_evaluation_ready": True, "epoch_digest": "sha256:epoch",
        "git_head": "0123456789abcdef", "git_dirty": False,
        "toolchain_manifest_digest": "toolchain-digest",
        "memory_bundle_digest": "sha256:empty-m0", "blockers": [],
    }
    monkeypatch.setattr(seed_m0, "verify_research_epoch", lambda path: checked_epoch)
    monkeypatch.setattr(
        seed_m0, "_current_source_identity",
        lambda: {"repo": str(tmp_path), "git_head": "0123456789abcdef",
                 "git_dirty": False})
    spec = {
        "schema": seed_m0.SCHEMA, "campaign_id": "seed-m0-test",
        "target_scope": "flow_feasibility",
        "materialized_at": "2026-09-22T17:15:00+00:00",
        "model_call_limit": 0, "online_memory_update": False,
        "production_authority": False,
        "epoch": {"path": str(epoch), "epoch_digest": "sha256:epoch",
                  "git_head": "0123456789abcdef"},
        "pairs": [first, second],
    }
    spec_path = tmp_path / "m0-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    output = tmp_path / "m0"
    result = seed_m0.build_research_seed_m0(spec=spec_path, output=output)
    assert result["m0_status"] == "BUILT_READ_ONLY_RESEARCH"
    assert result["source_groups"] == ["owner:a", "owner:b"]
    assert result["replication"]["eligible"] is True
    assert result["knowledge"]["status"] == "validated"
    assert result["asset_status"] == "candidate"
    assert result["consumption_preflight"]["selection"]["decision"] == "SELECT"
    assert result["production_authority"] is False
    assert result["model_calls"] == 0
    assert verify_bundle(output)["ok"] is True
    assert stat.S_IMODE(
        (output / "closed_loop/tehm.sqlite").stat().st_mode) == 0o444
    report = json.loads(
        (output / "research/m0-build-report.json").read_text(encoding="utf-8"))
    assert report["knowledge_authority"]["eligible"] is True
    assert report["research_epoch"]["epoch_digest"] == "sha256:epoch"
    replay = seed_m0.build_research_seed_m0(
        spec=spec_path, output=tmp_path / "m0-replay")
    assert replay["bundle_digest"] == result["bundle_digest"]
