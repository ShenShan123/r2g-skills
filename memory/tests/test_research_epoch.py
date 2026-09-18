from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tehm import db
from tehm.evaluation.research_epoch import (
    ResearchEpochError,
    freeze_research_epoch,
    verify_research_epoch,
)
from tehm.orfs_toolchain import build_toolchain_manifest
from tehm.sync import export_bundle
from scripts.run_orfs_diversity_campaign import preflight_orfs_toolchain


MEMORY_ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _fake_orfs(root: Path) -> dict:
    (root / "flow" / "scripts").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n", encoding="utf-8")
    (root / "flow" / "scripts" / "synth_canonicalize.tcl").write_text(
        "# probe\n", encoding="utf-8"
    )
    for name, text in (("openroad", "OpenROAD test"), ("yosys", "Yosys test")):
        path = root / "tools" / "install" / name / "bin" / name
        path.parent.mkdir(parents=True)
        path.write_text(f"#!/bin/sh\necho '{text}'\n", encoding="utf-8")
        path.chmod(0o755)
    return preflight_orfs_toolchain({"orfs_root": str(root)}, env={})


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture()
def epoch_inputs(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "research@example.invalid")
    _git(repo, "config", "user.name", "Research Test")
    _git(repo, "add", "src/tracked.py")
    _git(repo, "commit", "-m", "initial")
    (repo / "src" / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "src" / "untracked.py").write_text("EXTRA = True\n", encoding="utf-8")

    toolchain_path = tmp_path / "toolchain.json"
    _write_json(toolchain_path, build_toolchain_manifest(_fake_orfs(tmp_path / "orfs")))
    memory_db = tmp_path / "memory.sqlite"
    connection = db.connect(memory_db)
    db.ensure_schema(connection)
    connection.commit()
    connection.close()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    memory_snapshot = tmp_path / "m0"
    export_bundle(
        output=memory_snapshot,
        db_path=memory_db,
        artifact_root=artifact_root,
        metadata={"fixture": "research-epoch"},
    )
    schema_path = MEMORY_ROOT / "tehm" / "schema.sql"
    oracle = tmp_path / "oracle.py"
    oracle.write_text("def check():\n    return True\n", encoding="utf-8")
    controller = tmp_path / "controller.json"
    _write_json(controller, {
        "schema": "tehm-research-controller-v1",
        "production_authority": False,
        "online_memory_update": False,
    })
    budget = tmp_path / "budget.json"
    _write_json(budget, {
        "schema": "tehm-research-budget-v1",
        "candidate_limit": 3,
        "eda_call_limit": 1,
        "model_call_limit": 0,
        "model_token_limit": 0,
        "wallclock_limit_seconds": 30,
        "retry_limit": 0,
    })
    evidence = tmp_path / "evidence.json"
    _write_json(evidence, {
        "schema": "tehm-research-evidence-status-v1",
        "entries": [{
            "evidence_id": "selected",
            "classification": "valid",
            "selected_for_epoch": True,
            "basis": "test authority",
        }],
    })
    return {
        "repo_root": repo,
        "toolchain_manifest": toolchain_path,
        "memory_snapshot": memory_snapshot,
        "schema_path": schema_path,
        "oracle_paths": [oracle],
        "controller_config": controller,
        "budget_config": budget,
        "evidence_status": evidence,
        "source_roots": ["src"],
    }


def test_research_epoch_freezes_dirty_source_and_replays(tmp_path, epoch_inputs):
    output = tmp_path / "epoch"
    result = freeze_research_epoch(
        **epoch_inputs,
        output=output,
        epoch_id="test-epoch",
    )
    assert result["valid"] is True
    assert result["research_evaluation_ready"] is True
    assert result["git_dirty"] is True
    source = json.loads((output / "source/source-manifest.json").read_text())
    assert source["tracked_file_count"] == 1
    assert source["untracked_file_count"] == 1
    assert (output / "source/working-source.tar").is_file()
    assert verify_research_epoch(output) == result


def test_research_epoch_refuses_repo_output_and_detects_drift(tmp_path, epoch_inputs):
    with pytest.raises(ResearchEpochError, match="outside"):
        freeze_research_epoch(
            **epoch_inputs,
            output=epoch_inputs["repo_root"] / "epoch",
            epoch_id="bad-output",
        )
    output = tmp_path / "epoch"
    freeze_research_epoch(**epoch_inputs, output=output, epoch_id="drift")
    (output / "evidence-status.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ResearchEpochError, match="drifted"):
        verify_research_epoch(output)


def test_research_epoch_blocks_selected_nonvalid_evidence(tmp_path, epoch_inputs):
    evidence = Path(epoch_inputs["evidence_status"])
    _write_json(evidence, {
        "schema": "tehm-research-evidence-status-v1",
        "entries": [{
            "evidence_id": "pending",
            "classification": "pending_replay",
            "selected_for_epoch": True,
            "basis": "not replayed",
        }],
    })
    result = freeze_research_epoch(
        **epoch_inputs,
        output=tmp_path / "epoch",
        epoch_id="blocked",
    )
    assert result["research_evaluation_ready"] is False
    assert result["blockers"] == ["selected_evidence_not_valid:pending"]
