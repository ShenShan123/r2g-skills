"""Tests for the read-only ORFS routing semantic preflight."""
from __future__ import annotations

from pathlib import Path

from tehm.physical.orfs_preflight import (
    inspect_routing_layer_adjustment, preflight_digest)


def _orfs_root(tmp_path: Path, platform: str, text: str) -> Path:
    hook = tmp_path / "flow" / "platforms" / platform / "fastroute.tcl"
    hook.parent.mkdir(parents=True)
    hook.write_text(text)
    return tmp_path


def test_hardcoded_platform_hook_is_noop_and_content_addressed(tmp_path):
    root = _orfs_root(
        tmp_path, "sky130hs",
        "set_global_routing_layer_adjustment $::env(MIN_ROUTING_LAYER)-"
        "$::env(MAX_ROUTING_LAYER) 0.2\n")
    result = inspect_routing_layer_adjustment(
        "sky130hs", {"ROUTING_LAYER_ADJUSTMENT": "0.05"},
        orfs_root=root)
    assert result["status"] == "NO_OP"
    assert result["applicability"] == "INAPPLICABLE"
    assert result["reason"] == "routing_hook_overrides_config_knob"
    assert len(result["hook_sha256"]) == 64
    assert preflight_digest(result) == preflight_digest(dict(result))


def test_env_driven_platform_hook_is_effective(tmp_path):
    root = _orfs_root(
        tmp_path, "asap7",
        "set_global_routing_layer_adjustment $::env(MIN_ROUTING_LAYER)-"
        "$::env(MAX_ROUTING_LAYER) $::env(ROUTING_LAYER_ADJUSTMENT)\n")
    result = inspect_routing_layer_adjustment(
        "asap7", {"ROUTING_LAYER_ADJUSTMENT": "0.05"},
        orfs_root=root)
    assert result["status"] == "EFFECTIVE"
    assert result["reason"] == "routing_hook_consumes_config_knob"


def test_configured_make_hook_is_resolved_against_orfs_root(tmp_path):
    hook = (tmp_path / "flow" / "designs" / "sky130hs" / "fifo" /
            "fastroute.tcl")
    hook.parent.mkdir(parents=True)
    hook.write_text(
        "set_global_routing_layer_adjustment "
        "$::env(MIN_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER) 0.2\n")
    result = inspect_routing_layer_adjustment(
        "sky130hs", {"ROUTING_LAYER_ADJUSTMENT": "0.05"},
        config={"FASTROUTE_TCL": "$(DESIGN_HOME)/$(PLATFORM)/"
                                "$(DESIGN_NICKNAME)/fastroute.tcl",
                "DESIGN_NICKNAME": "fifo"},
        orfs_root=tmp_path)
    assert result["status"] == "NO_OP"
    assert result["configured_hook"].startswith("$(DESIGN_HOME)")
    assert result["hook"] == str(hook.resolve())


def test_missing_hook_is_unknown_and_fail_closed_when_root_is_declared(tmp_path):
    result = inspect_routing_layer_adjustment(
        "sky130hs", {"ROUTING_LAYER_ADJUSTMENT": "0.05"},
        orfs_root=tmp_path)
    assert result["status"] == "UNKNOWN"
    assert result["enforced"] is True
    assert result["reason"] == "routing_hook_unavailable"


def test_compatibility_fixture_without_orfs_root_is_not_checked():
    result = inspect_routing_layer_adjustment(
        "nangate45", {"ROUTING_LAYER_ADJUSTMENT": "0.05"})
    assert result["status"] == "NOT_CHECKED"
    assert result["enforced"] is False


def test_non_routing_action_is_not_applicable():
    result = inspect_routing_layer_adjustment(
        "sky130hs", {"CORE_UTILIZATION": "30"},
        orfs_root="/does/not/matter")
    assert result["status"] == "NOT_APPLICABLE"
    assert result["enforced"] is False
