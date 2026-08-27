from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "memory" / "scripts" / "run_orfs_diversity_campaign.py"
sys.path.insert(0, str(REPO / "memory" / "scripts"))
from run_orfs_diversity_campaign import capture_pairs  # noqa: E402
from tehm.batch_lane import BatchLaneError  # noqa: E402


def test_prepare_has_disjoint_heldout_and_platform_family_matrix(tmp_path):
    orfs = tmp_path / "orfs"
    templates = (("sky130hs", "gcd", "gcd"),
                 ("sky130hs", "aes", "aes_cipher_top"),
                 ("gf180", "jpeg", "jpeg_encoder"),
                 ("gf180", "riscv32i", "riscv"),
                 ("ihp-sg13g2", "spi", "spi"))
    for platform, design, top in templates:
        directory = orfs / "flow" / "designs" / platform / design
        directory.mkdir(parents=True)
        (directory / "constraint.sdc").write_text("create_clock -period 10 clk\n")
        (directory / "config.mk").write_text(
            f"export DESIGN_NICKNAME = {design}\n"
            f"export DESIGN_NAME = {top}\n"
            f"export PLATFORM = {platform}\n"
            "export CORE_UTILIZATION = 40\n"
            "export IO_CONSTRAINTS = /stock/io.tcl\n")
    root = tmp_path / "campaign"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--phase", "prepare", "--root", str(root),
         "--orfs-root", str(orfs)], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((root / "campaign_manifest.json").read_text())
    assert len(manifest["items"]) == 8
    assert {row["family"] for row in manifest["items"]} == {
        "DENSITY_RELIEF", "ROUTING_CAPACITY_RECOVERY"}
    assert {row["platform"] for row in manifest["items"]} == {"sky130hs", "gf180"}
    assert manifest["firewall"]["disjoint"] is True
    assert manifest["heldout"]["capturable"] is False
    assert manifest["heldout"]["lineage_id"] == "orfs-heldout:spi"
    assert manifest["heldout"]["platform"] == "ihp-sg13g2"
    assert manifest["heldout"]["baseline_core_utilization"] == 70
    assert manifest["heldout"]["lineage_id"] not in {
        row["lineage_id"] for row in manifest["items"]}
    gf_config = Path(next(row["before_project"] for row in manifest["items"]
                          if row["platform"] == "gf180")) / "constraints" / "config.mk"
    assert "export IO_CONSTRAINTS = \n" in gf_config.read_text()


def test_heldout_phase_preserves_captured_training_rows(tmp_path):
    orfs = tmp_path / "orfs"
    template = orfs / "flow" / "designs" / "ihp-sg13g2" / "spi"
    template.mkdir(parents=True)
    (template / "constraint.sdc").write_text("create_clock -period 10 clk\n")
    (template / "config.mk").write_text(
        "export DESIGN_NAME = spi\nexport PLATFORM = ihp-sg13g2\n"
        "export CORE_UTILIZATION = 20\n")
    root = tmp_path / "campaign"
    root.mkdir()
    original = {
        "campaign_version": "old", "orfs_root": "/old",
        "items": [{"lineage_id": "orfs-v2:gcd"}],
        "captured": [{"case_id": "keep", "transition_id": "transition_keep"}],
        "heldout": {"lineage_id": "orfs-heldout:old"},
        "firewall": {},
    }
    (root / "campaign_manifest.json").write_text(json.dumps(original))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--phase", "heldout", "--root", str(root),
         "--orfs-root", str(orfs)], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((root / "campaign_manifest.json").read_text())
    assert manifest["captured"] == original["captured"]
    assert manifest["heldout"]["lineage_id"] == "orfs-heldout:spi"
    assert manifest["firewall"]["disjoint"] is True


def test_capture_quarantines_incomplete_oracle_from_learner(tmp_path):
    """A route-only success must not become training support."""
    root = tmp_path / "campaign"
    before = root / "cases" / "before"
    after = root / "cases" / "after"
    for project, run_tag, util in ((before, "RUN_before", "50"),
                                   (after, "RUN_after", "40")):
        (project / "constraints").mkdir(parents=True)
        (project / "reports").mkdir()
        run = project / "backend" / run_tag
        run.mkdir(parents=True)
        (project / "constraints" / "config.mk").write_text(
            "export DESIGN_NAME = gcd\nexport PLATFORM = sky130hs\n"
            f"export CORE_UTILIZATION = {util}\n")
        (run / "run-meta.json").write_text(json.dumps({
            "run_tag": run_tag, "make_status": 0,
            "config_mk": str(project / "constraints" / "config.mk")}))
        (run / "stage_log.jsonl").write_text(
            json.dumps({"stage": "route", "status": 0}) + "\n" +
            json.dumps({"stage": "finish", "status": 0}) + "\n")
        (project / "reports" / "route.json").write_text(
            json.dumps({"status": "clean"}))

    manifest = {
        "items": [{
            "case_id": "sky130hs:gcd:ROUTING_CAPACITY_RECOVERY:default->0.05",
            "lineage_id": "orfs-test:sky130hs:gcd",
            "platform": "sky130hs", "family": "ROUTING_CAPACITY_RECOVERY",
            "check": "route", "config_edits": {"ROUTING_LAYER_ADJUSTMENT": "0.05"},
            "before_project": str(before), "after_project": str(after),
        }],
        "heldout": {"lineage_id": "orfs-heldout:spi"},
        "captured": [],
    }
    manifest_path = root / "campaign_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest))
    staging_db = root / "staging" / "tehm.sqlite"
    capture_pairs(manifest_path, manifest, staging_db,
                  root / "staging" / "artifacts")

    captured = manifest["captured"][0]
    assert captured["oracle_complete"] is False
    assert captured["dataset_split"] == "calibration"
    assert captured["learner_eligible"] is False
    import sqlite3
    conn = sqlite3.connect(staging_db)
    assert conn.execute(
        "SELECT split, learner_eligible FROM tehm_dataset_membership"
    ).fetchone() == ("calibration", 0)
    # Simulate a pre-gate campaign that incorrectly left an eligible row.  The
    # new strict capture must refuse to append a calibration row beside it,
    # because learner queries use EXISTS over campaign memberships.
    transition_id = captured["transition_id"]
    conn.execute(
        "INSERT INTO tehm_dataset_membership "
        "(transition_id,campaign_id,split,learner_eligible,assigned_at) "
        "VALUES (?,?,?,?,?)",
        (transition_id, "legacy", "training", 1, "2026-08-27T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    with pytest.raises(BatchLaneError, match="conflicts with existing learner"):
        capture_pairs(manifest_path, manifest, staging_db,
                      root / "staging" / "artifacts")
