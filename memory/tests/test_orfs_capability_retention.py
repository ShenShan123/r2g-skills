"""Frozen candidate policy retention stays read-only and fail-closed."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from tehm import db
from tehm.capability import create_policy_snapshot, record_policy_load
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_orfs_capability_retention import build_orfs_capability_retention  # noqa: E402


def _project(root: Path, name: str, *, failed: bool) -> Path:
    project = root / name
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir()
    run = project / "backend" / f"RUN_{name}"
    run.mkdir(parents=True)
    (project / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = sky130hs\n"
        f"export CORE_UTILIZATION = {'95' if failed else '40'}\n")
    (run / "run-meta.json").write_text(json.dumps({
        "run_tag": f"RUN_{name}", "make_status": 2 if failed else 0,
        "config_mk": str(project / "constraints" / "config.mk")}))
    (run / "stage_log.jsonl").write_text(
        json.dumps({"stage": "route", "status": 2 if failed else 0}) + "\n")
    for report, payload in {
        "route": {"status": "fail" if failed else "clean"},
        "drc": {"status": "clean"}, "lvs": {"status": "clean"},
        "timing_check": {"tier": "clean"},
        "ppa": {"summary": {"timing": {"setup_wns": 1.0},
                              "area": {"design_area_um2": 100.0}}},
    }.items():
        (project / "reports" / f"{report}.json").write_text(json.dumps(payload))
    return project


def test_real_orfs_retention_replay_is_read_only(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    source = tmp_path / "source.sqlite"
    destination = sqlite3.connect(source)
    conn.backup(destination)
    destination.close()
    conn.close()

    candidate_db = tmp_path / "candidate.sqlite"
    candidate = db.connect(candidate_db)
    db.ensure_schema(candidate)
    policy = create_policy_snapshot(
        candidate, memory_snapshot_id="candidate-memory",
        promoted_rules=[], retrieval_config={"evaluation_only": True})
    record_policy_load(candidate, policy_snapshot_id=policy.policy_snapshot_id,
                       runtime_id="runtime", loaded=True)
    candidate.close()
    report_path = tmp_path / "attribution.json"
    report_path.write_text(json.dumps({
        "derived_db": str(candidate_db),
        "candidate_policy": policy.to_dict(),
        "capability": {"capability_id": "capability-test"},
        "firewall": {"training_lineages": ["train:a"],
                      "heldout_lineages": ["held:b"]},
    }))
    before = _project(tmp_path, "retention_before", failed=True)
    after = _project(tmp_path, "retention_after", failed=False)
    digest_before = hashlib.sha256(candidate_db.read_bytes()).hexdigest()
    result = build_orfs_capability_retention(
        report_path, output=tmp_path / "retention.json",
        before_project=before, after_project=after,
        lineage_id="retention:c", config_edits={"CORE_UTILIZATION": "40"})
    assert result["retention"]["retained"] is True
    assert result["firewall"]["disjoint"] is True
    assert hashlib.sha256(candidate_db.read_bytes()).hexdigest() == digest_before
