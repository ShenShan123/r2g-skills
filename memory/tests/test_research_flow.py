from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tehm.evaluation.research_flow import (
    ResearchFlowError,
    stage_flow_project,
    verify_staged_flow_project,
)
from tehm.evaluation.research_inventory import (
    bind_research_inventory_adapters,
    build_research_inventory,
)


def _adapted_inventory(tmp_path: Path) -> Path:
    authority = tmp_path / "orfs"
    project = authority / "flow/designs/src/gcd"
    project.mkdir(parents=True)
    (project / "gcd.v").write_text(
        "module gcd(input wire clk, output wire done); assign done = clk; endmodule\n",
        encoding="utf-8",
    )
    (project / "src_manifest.txt").write_text("gcd.v\n", encoding="utf-8")
    (project / "design_meta.json").write_text(json.dumps({
        "design": "gcd", "top": "gcd", "notes": "repo=orfs-test",
    }), encoding="utf-8")
    support = authority / "flow/designs/test/gcd"
    support.mkdir(parents=True)
    (support / "config.mk").write_text(
        "export DESIGN_NAME = wrong\n"
        "export PLATFORM = wrong\n"
        "export VERILOG_FILES = /old/source.v\n"
        "export SDC_FILE = /old/constraint.sdc\n"
        "export CORE_UTILIZATION = 50\n",
        encoding="utf-8",
    )
    (support / "constraint.sdc").write_text(
        "create_clock -name clk -period 10 [get_ports clk]\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(authority)], check=True)
    subprocess.run(
        ["git", "-C", str(authority), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(authority), "config", "user.name", "Research Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(authority), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(authority), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(authority), "rev-parse", "HEAD"], check=True,
        text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    source_inventory = tmp_path / "source-inventory"
    source_result = build_research_inventory(
        corpus_root=authority / "flow/designs/src", output=source_inventory
    )
    spec = tmp_path / "adapter.json"
    spec.write_text(json.dumps({
        "schema": "tehm-research-inventory-adapter-spec-v1",
        "adapter_id": "test-flow-adapter-v1",
        "source_inventory_digest": source_result["inventory_digest"],
        "authority_checkout": {
            "git_head": head,
            "require_clean": True,
            "source_subtree": "flow/designs/src",
        },
        "designs": [{
            "design_id": "gcd",
            "role": "official_control",
            "top_module": "gcd",
            "ordered_filelist": ["gcd.v"],
            "include_dirs": [],
            "defines": {},
            "top_parameters": {},
            "support_files": [
                "flow/designs/test/gcd/config.mk",
                "flow/designs/test/gcd/constraint.sdc",
            ],
            "flow_binding": {
                "platform": "test",
                "config_template": "flow/designs/test/gcd/config.mk",
                "sdc_template": "flow/designs/test/gcd/constraint.sdc",
                "overrides": {"CORE_UTILIZATION": "45"},
            },
        }],
    }), encoding="utf-8")
    adapted = tmp_path / "adapted-inventory"
    bind_research_inventory_adapters(
        inventory=source_inventory, adapter_spec=spec,
        authority_root=authority, output=adapted,
    )
    return adapted


def test_stage_flow_project_is_source_bound_and_replayable(tmp_path: Path) -> None:
    inventory = _adapted_inventory(tmp_path)
    project = tmp_path / "campaign/project-gcd"
    result = stage_flow_project(inventory=inventory, design_id="gcd", output=project)

    assert result["valid"] is True
    assert result["source_unchanged"] is True
    assert result["logic_changes"] == []
    assert result["stub_generated"] is False
    assert (project / "rtl/gcd.v").is_file()
    config = (project / "constraints/config.mk").read_text(encoding="utf-8")
    assert "export DESIGN_NAME = gcd" in config
    assert "export DESIGN_NICKNAME = gcd" in config
    assert "export PLATFORM = test" in config
    assert f"export VERILOG_FILES = {project}/rtl/gcd.v" in config
    assert f"export SDC_FILE = {project}/constraints/constraint.sdc" in config
    assert "export CORE_UTILIZATION = 45" in config
    assert "/old/source.v" not in config
    assert verify_staged_flow_project(project)["valid"] is True

    (project / "backend").mkdir()
    (project / "backend/output.log").write_text("later output\n", encoding="utf-8")
    assert verify_staged_flow_project(project)["valid"] is True


def test_stage_flow_project_detects_input_tamper(tmp_path: Path) -> None:
    inventory = _adapted_inventory(tmp_path)
    project = tmp_path / "campaign/project-gcd"
    stage_flow_project(inventory=inventory, design_id="gcd", output=project)
    (project / "rtl/gcd.v").write_text("module changed; endmodule\n", encoding="utf-8")
    with pytest.raises(ResearchFlowError, match="staged input drifted"):
        verify_staged_flow_project(project)


def test_stage_flow_project_refuses_overwrite(tmp_path: Path) -> None:
    inventory = _adapted_inventory(tmp_path)
    project = tmp_path / "campaign/project-gcd"
    stage_flow_project(inventory=inventory, design_id="gcd", output=project)
    with pytest.raises(ResearchFlowError, match="refusing to overwrite"):
        stage_flow_project(inventory=inventory, design_id="gcd", output=project)
