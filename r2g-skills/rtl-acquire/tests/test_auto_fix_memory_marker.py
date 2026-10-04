"""auto_fix must recognise expand's own memory-guard marker.

Regression (wave-3 E5L Q4R, 2026-09-23): `expand_candidates.py` records a
memory-guard abort as the structured tail `memory_limit observed_bits=N
cap_bits=C next_cap_bits=M`, and the notes it writes carry no Yosys "Synthesized
memory size" text. `auto_fix`'s pattern only knew the Yosys wording, so the one
deterministic repair built for this failure class (raise SYNTH_MEMORY_MAX_BITS)
never fired: 7/7 memory-guard failures were retried, if at all, at the same cap.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "repair" / "auto_fix_failures.py"

EXPAND_NOTE = ("yosys: memory guard abort | Memories found in the design: | "
               "memory_limit observed_bits=4096 cap_bits=None next_cap_bits=8192")


def _load():
    spec = importlib.util.spec_from_file_location("auto_fix_memory_marker_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_patterns_match_the_structured_marker_alone() -> None:
    module = _load()
    assert "synthesized memory size" not in EXPAND_NOTE.lower()
    assert module.MEMORY_LIMIT_RE.search(EXPAND_NOTE)
    assert module.classify_failure_family(EXPAND_NOTE)["failure_class"] == "memory_limit"


def test_memory_marker_produces_a_raised_cap_retry(tmp_path: Path, monkeypatch) -> None:
    module = _load()
    source = tmp_path / "fifo_core.v"
    source.write_text("module fifo_core; endmodule\n", encoding="ascii")
    index = tmp_path / "index.csv"
    _write_csv(index, ["design", "status", "source_path", "notes"], [
        {"design": "fifo_core", "status": "synth_failed",
         "source_path": str(source), "notes": EXPAND_NOTE}])
    empty = ["design", "source_path"]
    _write_csv(tmp_path / "exclude.csv", empty, [])
    _write_csv(tmp_path / "defer.csv", empty, [])
    plan = tmp_path / "plan.json"
    retry_out = tmp_path / "retry.csv"
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT),
        "--index-csv", str(index),
        "--exclude-csv", str(tmp_path / "exclude.csv"),
        "--defer-csv", str(tmp_path / "defer.csv"),
        "--plan-json", str(plan),
        "--retry-csv", str(tmp_path / "missing-retry.csv"),
        "--retry-autofix-csv", str(retry_out),
        "--out-root", str(tmp_path / "out"),
        "--stub-dir", str(tmp_path / "stubs"),
        "--strategy-json", str(tmp_path / "missing-strategy.json"),
        "--deny-policy-json", str(tmp_path / "missing-deny.json"),
        "--repair-log-json", str(tmp_path / "repair-log.json"),
        "--design-scores-csv", str(tmp_path / "missing-scores.csv"),
        "--scan-state-json", str(tmp_path / "missing-scan.json"),
        "--signatures-json", str(tmp_path / "signatures.json"),
        "--signature-actions-json", str(tmp_path / "missing-actions.json"),
        "--failure-families-json", str(tmp_path / "families.json"),
        "--candidates-dir", str(tmp_path / "candidates"),
    ])

    module.main()

    assert json.loads(plan.read_text(encoding="utf-8"))["auto_fixed"] == 1
    with retry_out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert int(rows[0]["synth_memory_max_bits"]) >= 8192
