from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "memory" / "scripts" / "run_orfs_diversity_campaign.py"


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
