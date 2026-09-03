"""Unit checks for the explicit external-ORFS challenge producer."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_r3_orfs_interference_challenge import (
    OrfsInterferenceChallengeError, _candidate, _config_values,
)


def _project(tmp_path: Path, *, include_sources: bool = True) -> Path:
    project = tmp_path / "orfs-project"
    (project / "constraints").mkdir(parents=True)
    source = tmp_path / "source.v"
    source.write_text("module source; endmodule\n")
    files = f"export VERILOG_FILES = {source}\n" if include_sources else ""
    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = uart\n"
        "export PLATFORM = sky130hs\n" + files)
    return project


def test_config_parser_requires_explicit_external_verilog_inputs(tmp_path):
    project = _project(tmp_path)
    assert _config_values(project)[:2] == ("uart", "sky130hs")
    assert "source.v" in _config_values(project)[2]

    missing = _project(tmp_path / "missing", include_sources=False)
    with pytest.raises(OrfsInterferenceChallengeError, match="VERILOG_FILES"):
        _config_values(missing)


def test_challenge_candidate_is_evaluation_only_config_delta():
    candidate = _candidate("uart", core_utilization="99")
    assert candidate.evaluation_only is True
    assert candidate.concrete_action["domain"] == "flow.CONFIG_DELTA"
    assert candidate.concrete_action["payload"]["config_edits"] == {
        "CORE_UTILIZATION": "99"}
    assert candidate.provenance["canonical_memory_mutation"] == "none"
