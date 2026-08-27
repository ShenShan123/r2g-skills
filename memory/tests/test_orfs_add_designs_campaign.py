"""Provenance gates for the add-designs ORFS campaign."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "memory" / "scripts"))

from run_orfs_add_designs_campaign import (  # noqa: E402
    build_source_freeze,
    prepare,
)
from run_orfs_diversity_campaign import capture_pairs  # noqa: E402
from tehm.batch_lane import BatchLaneError  # noqa: E402
from tehm.batch_lane import _input_binding, _timing_contract  # noqa: E402


def _kwargs(sdc: Path) -> dict:
    return {
        "designs": ("uart",),
        "platforms": ("sky130hs",),
        "families": ("DENSITY_RELIEF",),
        "indexes": 1,
        "core_utils": (50,),
        "lineage_prefix": "freeze-test",
        "rtl_override_path": None,
        "sdc_override_path": sdc,
        "template_design": "gcd",
    }


def _fake_orfs(root: Path) -> Path:
    cfg = root / "flow" / "designs" / "sky130hs" / "gcd"
    cfg.mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n\t@true\n")
    (cfg / "config.mk").write_text(
        "export DESIGN_NAME = gcd\nexport PLATFORM = sky130hs\n")
    (cfg / "constraint.sdc").write_text("set clk_period 10\n")
    src = root / "flow" / "designs" / "src" / "uart"
    src.mkdir(parents=True)
    (src / "uart.v").write_text("module uart(input clk); endmodule\n")
    return root


def test_prepare_requires_source_freeze_and_binds_inputs(tmp_path):
    orfs = _fake_orfs(tmp_path / "orfs")
    override = tmp_path / "override.sdc"
    override.write_text("set clk_period 10\n")
    kwargs = _kwargs(override)
    with pytest.raises(BatchLaneError, match="source freeze is required"):
        prepare(tmp_path / "campaign", orfs, **kwargs)

    campaign = tmp_path / "campaign"
    build_source_freeze(campaign, orfs, **kwargs)
    manifest = prepare(campaign, orfs,
                       source_freeze=campaign / "source_freeze.json", **kwargs)
    assert manifest["source_freeze_digest"]
    item = manifest["items"][0]
    assert item["input_bindings"]["before"]["source_digest"]
    assert item["timing_contract"]["before"]["clock_period_ns"] == 10.0

    override.write_text("set clk_period 11\n")
    with pytest.raises(BatchLaneError, match="input digest mismatch"):
        prepare(campaign, orfs,
                source_freeze=campaign / "source_freeze.json", **kwargs)


def test_custom_rtl_top_is_bound_into_materialized_sdc(tmp_path):
    orfs = _fake_orfs(tmp_path / "orfs")
    rtl = tmp_path / "custom.v"
    rtl.write_text("module custom_top(input clk); endmodule\n")
    sdc = tmp_path / "custom.sdc"
    sdc.write_text("current_design template_top\nset clk_port_name clk\nset clk_period 10\n")
    kwargs = {
        "designs": ("custom",), "platforms": ("sky130hs",),
        "families": ("DENSITY_RELIEF",), "indexes": 1, "core_utils": (50,),
        "lineage_prefix": "top-bind", "rtl_override_path": rtl,
        "sdc_override_path": sdc, "template_design": "gcd",
    }
    campaign = tmp_path / "campaign"
    build_source_freeze(campaign, orfs, **kwargs)
    manifest = prepare(campaign, orfs,
                       source_freeze=campaign / "source_freeze.json", **kwargs)
    item = manifest["items"][0]
    assert item["top"] == "custom_top"
    bound = Path(item["before_project"]) / "constraints" / "constraint.sdc"
    assert "current_design custom_top" in bound.read_text()


def test_non_training_split_is_bound_into_source_freeze(tmp_path):
    """Changing a held-out campaign back to training requires a new freeze."""
    orfs = _fake_orfs(tmp_path / "orfs")
    override = tmp_path / "override.sdc"
    override.write_text("set clk_period 10\n")
    kwargs = {
        **_kwargs(override),
        "dataset_split": "heldout",
    }
    campaign = tmp_path / "heldout-campaign"
    build_source_freeze(campaign, orfs, **kwargs)
    manifest = prepare(campaign, orfs,
                       source_freeze=campaign / "source_freeze.json", **kwargs)
    assert manifest["dataset_split"] == "heldout"
    assert manifest["items"][0]["dataset_split"] == "heldout"
    training_kwargs = {**kwargs, "dataset_split": "training"}
    with pytest.raises(BatchLaneError, match="source freeze request mismatch"):
        prepare(campaign, orfs,
                source_freeze=campaign / "source_freeze.json",
                **training_kwargs)


def test_semantic_oracle_is_source_frozen_and_materialized(tmp_path):
    orfs = _fake_orfs(tmp_path / "orfs")
    override = tmp_path / "override.sdc"
    override.write_text("set clk_period 10\n")
    semantic = tmp_path / "semantic.json"
    semantic.write_text(json.dumps({
        "version": "orfs-semantic-oracle-v1",
        "kind": "config_numeric_bound",
        "config_key": "CORE_UTILIZATION",
        "operator": "le",
        "threshold": 65,
    }))
    kwargs = {
        **_kwargs(override),
        "semantic_oracle_path": semantic,
        "dataset_split": "heldout",
    }
    campaign = tmp_path / "semantic-campaign"
    build_source_freeze(campaign, orfs, **kwargs)
    manifest = prepare(campaign, orfs,
                       source_freeze=campaign / "source_freeze.json", **kwargs)
    assert manifest["semantic_oracle"]["threshold"] == 65.0
    assert manifest["items"][0]["semantic_oracle"] == manifest["semantic_oracle"]
    request = json.loads((campaign / "source_freeze.json").read_text())["request"]
    assert request["semantic_oracle"] == str(semantic.resolve())
    semantic.write_text(semantic.read_text().replace("65", "64"))
    with pytest.raises(BatchLaneError, match="input digest mismatch"):
        prepare(campaign, orfs,
                source_freeze=campaign / "source_freeze.json", **kwargs)


def _complete_run(project: Path, tag: str) -> None:
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir()
    run = project / "backend" / tag
    run.mkdir(parents=True)
    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = uart\nexport PLATFORM = sky130hs\n")
    (project / "constraints" / "constraint.sdc").write_text(
        "set clk_period 10\n")
    (run / "run-meta.json").write_text(json.dumps({
        "run_tag": tag, "make_status": 0,
        "config_mk": str(project / "constraints" / "config.mk")}))
    (run / "stage_log.jsonl").write_text(
        json.dumps({"stage": "route", "status": 0}) + "\n" +
        json.dumps({"stage": "finish", "status": 0}) + "\n")
    for name in ("route", "drc", "timing"):
        payload = {"status": "clean"}
        if name == "timing":
            payload = {"status": "clean", "tier": "clean"}
        (project / "reports" / f"{name if name != 'timing' else 'timing_check'}.json").write_text(
            json.dumps(payload))
    (project / "reports" / "ppa.json").write_text(json.dumps({}))


def test_capture_rechecks_manifest_input_binding(tmp_path):
    root = tmp_path / "campaign"
    before, after = root / "before", root / "after"
    _complete_run(before, "RUN_before")
    _complete_run(after, "RUN_after")
    rtl = root / "uart.v"
    rtl.write_text("module uart(input clk); endmodule\n")
    item = {
        "case_id": "sky130hs:uart:binding",
        "lineage_id": "freeze-test:sky130hs:uart",
        "platform": "sky130hs", "family": "DENSITY_RELIEF", "check": "route",
        "config_edits": {"CORE_UTILIZATION": "40"},
        "before_project": str(before), "after_project": str(after),
        "rtl_files": [str(rtl)],
        "input_bindings": {
            "before": _input_binding(before, [rtl]),
            "after": _input_binding(after, [rtl]),
        },
        "timing_contract": {
            "before": _timing_contract(before),
            "after": _timing_contract(after),
        },
    }
    # The execution evidence remains otherwise complete, but the prepared
    # source/config identity no longer matches the manifest.
    (after / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = uart\nexport PLATFORM = sky130hs\n"
        "export CORE_UTILIZATION = 40\n")
    manifest = {"items": [item], "heldout": {"lineage_id": "heldout:spi"},
                "captured": []}
    manifest_path = root / "campaign_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest))
    db = root / "staging" / "tehm.sqlite"
    capture_pairs(manifest_path, manifest, db, root / "staging" / "artifacts")
    row = manifest["captured"][0]
    assert row["oracle_complete"] is False
    assert row["dataset_split"] == "calibration"
    assert row["learner_eligible"] is False
    conn = __import__("sqlite3").connect(db)
    verifier = conn.execute(
        "SELECT verifier_json FROM tehm_transitions").fetchone()[0]
    conn.close()
    assert json.loads(verifier)["input_binding"]["verified"] is False
