"""Read-only procedural memory funnel audit tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_procedural_memory import audit  # noqa: E402


def test_procedural_audit_reports_group_to_rule_gap(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    result = audit(conn, out_dir=tmp_path)
    assert result["transition_count"] == 0
    assert result["rule_count"] == 0
    assert result["gaps"]["group_to_rule_conversion"] == "no_observed_group_loss"
    assert (tmp_path / "procedural_memory_audit.json").is_file()
