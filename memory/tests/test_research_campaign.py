from __future__ import annotations

import json
from pathlib import Path

import pytest

import tehm.evaluation.research_campaign as campaign_module
from tehm.evaluation.research_campaign import (
    ResearchCampaignError,
    prepare_research_campaign,
    verify_prepared_campaign,
)


CONTRACTS = Path(__file__).resolve().parents[1] / "evaluation/research_task_contracts_v1.json"


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    epoch = tmp_path / "epoch"
    inventory = tmp_path / "inventory"
    epoch_payload = {
        "schema": "tehm-research-epoch-v1",
        "epoch_id": "epoch-1",
        "epoch_digest": "sha256:epoch",
    }
    controller = {
        "schema": "tehm-research-controller-v1",
        "controller_id": "controller-1",
        "policies": ["no_persistent_memory", "legacy_memory", "tehm"],
    }
    budget = {
        "candidate_limit": 3,
        "eda_call_limit": 6,
        "model_call_limit": 0,
        "model_token_limit": 0,
        "wallclock_limit_seconds": 7200,
        "retry_limit": 1,
        "retryable_classes": ["infrastructure_transient"],
    }
    _write(epoch / "research-epoch.json", epoch_payload)
    _write(epoch / "bindings/controller.json", controller)
    _write(epoch / "bindings/budget.json", budget)

    designs = []
    for design_id in ("official", "external"):
        manifest = {
            "schema": "tehm-research-design-manifest-v1",
            "design_id": design_id,
            "project_path": f"catalog/{design_id}",
            "source_root": str(tmp_path / "RTL"),
            "source_bundle_digest": f"sha256:{design_id}",
            "manifest_digest": f"sha256:manifest-{design_id}",
            "compilation": {
                "top_module": "top",
                "top_authority": "explicit_config",
                "ordered_filelist": ["rtl/top.v"],
                "filelist_authority": "explicit:src_manifest.txt",
                "include_dirs": ["rtl"],
                "defines": {},
                "top_parameters": {},
            },
            "clock_and_constraints": {"clocks": [], "sdc_files": []},
            "verification": {"functional_repair_oracle": "unavailable"},
            "readiness": {"status": "NEEDS_ADAPTER"},
        }
        manifest_path = inventory / "design-manifests" / f"{design_id}.json"
        _write(manifest_path, manifest)
        designs.append({
            "design_id": design_id,
            "manifest_path": f"design-manifests/{design_id}.json",
            "manifest_digest": manifest["manifest_digest"],
        })
    inventory_payload = {
        "schema": "tehm-research-design-inventory-v1",
        "inventory_digest": "sha256:inventory",
        "designs": designs,
    }
    _write(inventory / "inventory.json", inventory_payload)

    monkeypatch.setattr(campaign_module, "verify_research_epoch", lambda path: {
        "valid": True,
        "research_evaluation_ready": True,
        "epoch_digest": "sha256:epoch",
    })
    monkeypatch.setattr(campaign_module, "verify_research_inventory", lambda path: {
        "valid": True,
        "corpus_unchanged": True,
        "inventory_digest": "sha256:inventory",
    })
    return epoch, inventory, budget


def _spec(path: Path, budget: dict, *, contract_id: str = "frontend_synthesis_preflight_v1") -> Path:
    payload = {
        "schema": "tehm-research-campaign-spec-v1",
        "campaign_id": "campaign-1",
        "profile": "controlled_action_pilot",
        "research_only": True,
        "policies": ["no_persistent_memory"],
        "budget": budget,
        "memory": {
            "online_update": False,
            "final_test_learning": False,
            "production_import": False,
        },
        "execution": {
            "original_corpus_read_only": True,
            "isolated_arm_workspaces": True,
            "overwrite_existing_attempt": False,
            "retain_failed_attempts": True,
            "audit_raw_reports": True,
        },
        "reporting": {
            "include_all_registered_tasks": True,
            "separate_unknown_and_failure": True,
            "separate_task_and_strict_signoff": True,
            "actual_cost_accounting": True,
        },
        "tasks": [
            {
                "task_id": "official-preflight",
                "design_id": "official",
                "role": "official_control",
                "contract_id": contract_id,
                "target_scope": {"kind": "frontend_synthesis"},
                "allowed_actions": ["NO_ACTION"],
                "observations": {"source": "frozen_manifest_only"},
            },
            {
                "task_id": "external-preflight",
                "design_id": "external",
                "role": "external_design",
                "contract_id": "frontend_synthesis_preflight_v1",
                "target_scope": {"kind": "frontend_synthesis"},
                "allowed_actions": ["NO_ACTION"],
                "observations": {"source": "frozen_manifest_only"},
            },
        ],
    }
    _write(path, payload)
    return path


def test_prepare_freezes_full_denominator_and_replays(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    epoch, inventory, budget = _inputs(tmp_path, monkeypatch)
    spec = _spec(tmp_path / "campaign.json", budget)
    output = tmp_path / "prepared"
    result = prepare_research_campaign(
        campaign_spec=spec,
        epoch=epoch,
        inventory=inventory,
        task_contracts=CONTRACTS,
        output=output,
    )
    assert result["valid"] is True
    assert result["task_count"] == 2
    assert result["silently_filtered"] == 0
    prepared = json.loads((output / "prepared-campaign.json").read_text())
    assert prepared["denominator"] == {
        "registered": 2, "prepared": 2, "silently_filtered": 0
    }
    assert prepared["authority"]["execution_started"] is False
    context = json.loads((output / "task-contexts/official-preflight.json").read_text())
    assert context["compile_input"]["design_manifest_digest"] == "sha256:manifest-official"
    assert context["contract"]["missing_report_verdict"] == "UNKNOWN"
    assert context["authority"]["future_execution_result_present"] is False
    assert verify_prepared_campaign(output)["valid"] is True


def test_prepare_rejects_unresolved_placeholder_and_gold(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    epoch, inventory, budget = _inputs(tmp_path, monkeypatch)
    spec = _spec(tmp_path / "campaign.json", budget)
    payload = json.loads(spec.read_text())
    payload["budget"]["wallclock_limit_seconds"] = "REQUIRED"
    _write(spec, payload)
    with pytest.raises(ResearchCampaignError, match="REQUIRED"):
        prepare_research_campaign(
            campaign_spec=spec, epoch=epoch, inventory=inventory,
            task_contracts=CONTRACTS, output=tmp_path / "prepared-a")
    payload["budget"]["wallclock_limit_seconds"] = 7200
    payload["tasks"][0]["gold_patch"] = "hidden"
    _write(spec, payload)
    with pytest.raises(ResearchCampaignError, match="gold_field"):
        prepare_research_campaign(
            campaign_spec=spec, epoch=epoch, inventory=inventory,
            task_contracts=CONTRACTS, output=tmp_path / "prepared-b")


def test_prepare_rejects_unverified_functional_oracle_without_filtering(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    epoch, inventory, budget = _inputs(tmp_path, monkeypatch)
    spec = _spec(
        tmp_path / "campaign.json", budget,
        contract_id="rtl_repair_functional_v1",
    )
    payload = json.loads(spec.read_text())
    payload["tasks"][0]["allowed_actions"] = ["CONFIG_EDIT"]
    _write(spec, payload)
    output = tmp_path / "prepared"
    with pytest.raises(ResearchCampaignError, match="verified functional repair oracle"):
        prepare_research_campaign(
            campaign_spec=spec, epoch=epoch, inventory=inventory,
            task_contracts=CONTRACTS, output=output)
    assert not output.exists()


def test_verify_detects_prepared_artifact_tamper(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    epoch, inventory, budget = _inputs(tmp_path, monkeypatch)
    spec = _spec(tmp_path / "campaign.json", budget)
    output = tmp_path / "prepared"
    prepare_research_campaign(
        campaign_spec=spec, epoch=epoch, inventory=inventory,
        task_contracts=CONTRACTS, output=output)
    roster = output / "task-roster.csv"
    roster.write_text(roster.read_text() + "tamper\n", encoding="utf-8")
    with pytest.raises(ResearchCampaignError, match="artifact drifted"):
        verify_prepared_campaign(output)
