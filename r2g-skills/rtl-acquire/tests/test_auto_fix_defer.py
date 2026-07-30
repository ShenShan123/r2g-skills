from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "repair"
    / "auto_fix_failures.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("auto_fix_defer_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_deferred_candidate_is_not_auto_fixed(tmp_path, monkeypatch):
    module = load_module()
    source = tmp_path / "core.sv"
    source.write_text("module core; endmodule\n", encoding="ascii")

    index = tmp_path / "index.csv"
    write_csv(
        index,
        ["design", "status", "source_path", "notes"],
        [
            {
                "design": "core",
                "status": "synth_failed",
                "source_path": str(source),
                "notes": "Synthesized memory size 8192 exceeds SYNTH_MEMORY_MAX_BITS",
            }
        ],
    )
    exclude = tmp_path / "exclude.csv"
    write_csv(exclude, ["design", "source_path"], [])
    defer = tmp_path / "defer.csv"
    write_csv(
        defer,
        ["design", "source_path"],
        [{"design": "core", "source_path": str(source)}],
    )

    plan = tmp_path / "plan.json"
    retry_out = tmp_path / "retry.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--index-csv",
            str(index),
            "--exclude-csv",
            str(exclude),
            "--defer-csv",
            str(defer),
            "--plan-json",
            str(plan),
            "--retry-csv",
            str(tmp_path / "missing-retry.csv"),
            "--retry-autofix-csv",
            str(retry_out),
            "--out-root",
            str(tmp_path / "out"),
            "--stub-dir",
            str(tmp_path / "stubs"),
            "--strategy-json",
            str(tmp_path / "missing-strategy.json"),
            "--deny-policy-json",
            str(tmp_path / "missing-deny.json"),
            "--repair-log-json",
            str(tmp_path / "repair-log.json"),
            "--design-scores-csv",
            str(tmp_path / "missing-scores.csv"),
            "--scan-state-json",
            str(tmp_path / "missing-scan.json"),
            "--signatures-json",
            str(tmp_path / "signatures.json"),
            "--signature-actions-json",
            str(tmp_path / "missing-actions.json"),
            "--failure-families-json",
            str(tmp_path / "families.json"),
            "--candidates-dir",
            str(tmp_path / "candidates"),
        ],
    )

    module.main()

    result = json.loads(plan.read_text(encoding="utf-8"))
    assert result["auto_fixed"] == 0
    assert result["auto_excluded"] == 0
    assert result["retry_candidates_csv"] == ""
    assert not retry_out.exists()
