"""Fail-closed checks for the scoped read-only S1 candidate preflight."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tehm.evaluation import research_s1_preflight as preflight


def test_observed_control_config_requires_one_u95_value(tmp_path: Path) -> None:
    config = tmp_path / "constraints/config.mk"
    config.parent.mkdir()
    config.write_text("export CORE_UTILIZATION = 95\n", encoding="utf-8")
    assert preflight._observed_utilization(tmp_path) == "95"
    config.write_text("export CORE_UTILIZATION = 40\n", encoding="utf-8")
    with pytest.raises(preflight.ResearchS1PreflightError, match="observed u95"):
        preflight._observed_utilization(tmp_path)
    config.write_text("export CORE_UTILIZATION = 95\nexport CORE_UTILIZATION = 95\n",
                      encoding="utf-8")
    with pytest.raises(preflight.ResearchS1PreflightError, match="one observed"):
        preflight._observed_utilization(tmp_path)


def test_preflight_refuses_overwrite_before_replay(tmp_path: Path) -> None:
    with pytest.raises(preflight.ResearchS1PreflightError, match="overwrite"):
        preflight.audit_s1_candidate_preflight(
            binding=tmp_path / "absent-binding", controls=tmp_path / "absent-controls",
            epoch=tmp_path / "absent-epoch", output=tmp_path)


def test_preflight_verifier_rejects_changed_digest(tmp_path: Path) -> None:
    report = {"schema": preflight.PREFLIGHT_SCHEMA,
              "preflight_digest": "sha256:incorrect"}
    (tmp_path / "preflight.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(preflight.ResearchS1PreflightError, match="digest mismatch"):
        preflight.verify_s1_candidate_preflight(tmp_path)
