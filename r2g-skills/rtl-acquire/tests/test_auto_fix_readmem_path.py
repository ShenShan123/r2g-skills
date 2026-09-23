"""auto_fix resolves a relative $readmem payload that Yosys cannot open.

Regression (wave-3 E5L, 2026-09-23): a repo kept `p1.hex` at its root because its
simulation ran from there, while `sv/main_mem.sv` loads `$readmemh("p1.hex", …)`.
Yosys only tries its CWD (the shared ORFS flow dir) and the consumer's own
directory, so synthesis failed on complete, correct RTL. The repair rewrites the
literal to the payload's absolute path in a COPY, only for a payload that exists
inside the same acquired repo; the acquired tree itself is never modified.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "repair" / "auto_fix_failures.py"


def _load():
    spec = importlib.util.spec_from_file_location("auto_fix_readmem_test", SCRIPT)
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


def _run(tmp_path: Path, monkeypatch, consumer: Path) -> tuple[dict, list[dict[str, str]]]:
    module = _load()
    note = (f"{consumer}:3: ERROR: Can not open file `p1.hex` for \\$readmemh. | "
            "Command exited with non-zero status 1")
    index = tmp_path / "index.csv"
    _write_csv(index, ["design", "status", "source_path", "notes"], [
        {"design": "sv_soc", "status": "synth_failed", "source_path": str(consumer),
         "notes": note}])
    for name in ("exclude.csv", "defer.csv"):
        _write_csv(tmp_path / name, ["design", "source_path"], [])
    plan, retry_out = tmp_path / "plan.json", tmp_path / "retry.csv"
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--index-csv", str(index),
        "--exclude-csv", str(tmp_path / "exclude.csv"),
        "--defer-csv", str(tmp_path / "defer.csv"),
        "--plan-json", str(plan), "--retry-csv", str(tmp_path / "missing-retry.csv"),
        "--retry-autofix-csv", str(retry_out), "--out-root", str(tmp_path / "out"),
        "--stub-dir", str(tmp_path / "stubs"),
        "--strategy-json", str(tmp_path / "missing-strategy.json"),
        "--deny-policy-json", str(tmp_path / "missing-deny.json"),
        "--repair-log-json", str(tmp_path / "repair-log.json"),
        "--design-scores-csv", str(tmp_path / "missing-scores.csv"),
        "--scan-state-json", str(tmp_path / "missing-scan.json"),
        "--signatures-json", str(tmp_path / "signatures.json"),
        "--signature-actions-json", str(tmp_path / "missing-actions.json"),
        "--failure-families-json", str(tmp_path / "families.json"),
        "--candidates-dir", str(tmp_path / "candidates")])
    module.main()
    rows: list[dict[str, str]] = []
    if retry_out.exists():
        with retry_out.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    return json.loads(plan.read_text(encoding="utf-8")), rows


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "_downloads" / "sv_soc_repo"
    consumer = repo / "sv" / "main_mem.sv"
    consumer.parent.mkdir(parents=True)
    consumer.write_text(
        "module main_mem(input logic clk, output logic [7:0] q);\n"
        "  logic [7:0] mem [0:15];\n"
        '  initial $readmemh("p1.hex", mem);\n'
        "  always_ff @(posedge clk) q <= mem[0];\nendmodule\n", encoding="utf-8")
    return repo, consumer


def test_repo_root_payload_is_rewritten_in_a_copy(tmp_path: Path, monkeypatch) -> None:
    repo, consumer = _repo(tmp_path)
    (repo / "p1.hex").write_text("00\n01\n", encoding="ascii")
    original = consumer.read_text(encoding="utf-8")

    plan, rows = _run(tmp_path, monkeypatch, consumer)

    assert plan["auto_fixed"] == 1 and len(rows) == 1
    rewritten = Path(rows[0]["rtl_files"].split(";")[0])
    assert rewritten != consumer
    assert f'$readmemh("{(repo / "p1.hex").resolve()}"' in rewritten.read_text(encoding="utf-8")
    assert consumer.read_text(encoding="utf-8") == original  # acquired tree untouched


def test_payload_outside_the_repo_is_not_used(tmp_path: Path, monkeypatch) -> None:
    _repo_dir, consumer = _repo(tmp_path)
    (tmp_path / "_downloads" / "p1.hex").write_text("ff\n", encoding="ascii")
    plan, rows = _run(tmp_path, monkeypatch, consumer)
    assert plan["auto_fixed"] == 0 and rows == []


def test_missing_payload_is_not_fixed(tmp_path: Path, monkeypatch) -> None:
    _repo_dir, consumer = _repo(tmp_path)
    plan, rows = _run(tmp_path, monkeypatch, consumer)
    assert plan["auto_fixed"] == 0 and rows == []
