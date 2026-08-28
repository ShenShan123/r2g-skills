from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_calibration_expansion import (  # noqa: E402
    _external_transition_id,
    _load_external_training,
    _persist_external_transition,
    _strict_oracle_one,
    _strict_oracle_projects,
    _subset_manifest,
)
from tehm import db as tehm_db
from tehm.artifact_store import ArtifactStore
from tehm.sync import canonical_json


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
    assert conn.execute("SELECT COUNT(*) FROM tehm_physical_effects").fetchone()[0] == 0
    conn.close()
