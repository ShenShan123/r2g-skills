from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SELECT_SCRIPT = ROOT / "scripts" / "acquire" / "select_expander_qualified_candidates.py"
SELECT_SPEC = importlib.util.spec_from_file_location("select_expander", SELECT_SCRIPT)
selector = importlib.util.module_from_spec(SELECT_SPEC)
assert SELECT_SPEC.loader is not None
SELECT_SPEC.loader.exec_module(selector)

ROUND_SCRIPT = ROOT / "scripts" / "run_expansion_round.py"
ROUND_SPEC = importlib.util.spec_from_file_location("run_expansion_round", ROUND_SCRIPT)
round_runner = importlib.util.module_from_spec(ROUND_SPEC)
assert ROUND_SPEC.loader is not None
ROUND_SPEC.loader.exec_module(round_runner)


FIELDS = [
    "source", "design", "priority", "expected_top", "source_path", "rtl_files",
    "include_dirs", "top_parameters", "resource_tier", "notes",
    "expander_bridge_manifest", "expander_design_id",
]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def build_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    specs = [
        ("d1", "r1", "uart_top", 200, "high"),
        ("d2", "r1", "spi_top", 2200, "high"),
        ("d3", "r2", "sha256_core", 22000, "medium"),
        ("d4", "r2", "fir_filter", 400, "medium"),
        ("d5", "r3", "axi_bridge", 4500, "low"),
        ("d6", "r4", "systolic_array", 45000, "high"),
        ("tiny", "r5", "counter", 20, "high"),
        ("huge", "r6", "ethernet_core", 120000, "high"),
        ("open", "r7", "memory_ctrl", 800, "high"),
    ]
    bridge = tmp_path / "bridge.json"
    bridge_rows = []
    candidate_rows = []
    for design, repo, top, _cells, priority in specs:
        bridge_rows.append({
            "design": design,
            "expander_design_id": f"exp_{design}",
            "family_id": f"family_{design}",
            "repository_url": f"https://github.com/example/{repo}",
            "top_module": top,
        })
        candidate_rows.append({
            "source": "rtl-expander", "design": design, "priority": priority,
            "expected_top": top, "source_path": f"/src/{design}.v",
            "rtl_files": f"/src/{design}.v", "include_dirs": "/src",
            "top_parameters": "", "resource_tier": "high" if design == "huge" else "normal",
            "notes": "", "expander_bridge_manifest": str(bridge),
            "expander_design_id": f"exp_{design}",
        })
    bridge.write_text(json.dumps({"candidates": bridge_rows}), encoding="utf-8")
    candidates = tmp_path / "candidates.csv"
    write_csv(candidates, candidate_rows)

    index = tmp_path / "index.csv"
    with index.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["design", "status", "cells"])
        writer.writeheader()
        for design, _repo, _top, cells, _priority in specs:
            writer.writerow({"design": design, "status": "success", "cells": cells})
            meta = tmp_path / "out" / design / "design_meta.json"
            meta.parent.mkdir(parents=True)
            unresolved = ["weights.hex"] if design == "open" else []
            meta.write_text(json.dumps({
                "platform": "sky130hd",
                "compile_manifest": {"unresolved_collateral": unresolved}
            }), encoding="utf-8")
    return candidates, index, tmp_path / "out"


@pytest.fixture(autouse=True)
def trust_synthetic_bridge(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        selector, "verify_bridge_path",
        lambda path: json.loads(path.read_text(encoding="utf-8")),
    )


def test_select_balances_repository_function_and_size_and_tracks_large(tmp_path: Path):
    candidates, index, out_root = build_inputs(tmp_path)
    selected_csv = tmp_path / "selected.csv"
    large_csv = tmp_path / "large.csv"
    result = selector.select(
        candidates, index, out_root, selected_csv, tmp_path / "selection.json",
        target=5, minimum_cells=100, maximum_cells_exclusive=100000,
        max_per_repository=2, platform="sky130hd", large_design_csv=large_csv,
    )
    assert result["target_met"] is True
    assert result["selected_count"] == 5
    assert max(result["selected_repository_counts"].values()) <= 2
    assert len(result["selected_repository_counts"]) >= 3
    assert len(result["selected_function_category_counts"]) >= 3
    assert set(result["selected_size_bucket_counts"]) == {
        "small_100_999", "medium_1000_9999", "large_10000_99999"
    }
    assert result["excluded_reason_counts"]["trivial_design"] == 1
    assert result["excluded_reason_counts"]["oversize_design"] == 1
    assert result["excluded_reason_counts"]["compilation_closure_incomplete"] == 1
    assert [row["design"] for row in csv.DictReader(large_csv.open())] == ["huge"]


def test_insufficient_pool_is_recorded_without_padding(tmp_path: Path):
    candidates, index, out_root = build_inputs(tmp_path)
    result = selector.select(
        candidates, index, out_root, tmp_path / "selected.csv", tmp_path / "selection.json",
        target=20, minimum_cells=100, maximum_cells_exclusive=100000,
        max_per_repository=1, platform="sky130hd",
    )
    assert result["target_met"] is False
    assert result["selected_count"] == 4


def test_platform_mismatch_cannot_enter_selection(tmp_path: Path):
    candidates, index, out_root = build_inputs(tmp_path)
    meta = out_root / "d6" / "design_meta.json"
    payload = json.loads(meta.read_text())
    payload["platform"] = "nangate45"
    meta.write_text(json.dumps(payload))
    result = selector.select(
        candidates, index, out_root, tmp_path / "selected.csv", tmp_path / "selection.json",
        target=20, minimum_cells=100, maximum_cells_exclusive=100000,
        max_per_repository=4, platform="sky130hd",
    )
    assert result["excluded_reason_counts"]["qualification_platform_mismatch"] == 1
    assert "d6" not in result["selected_designs"]


def test_cost_guard_defers_high_resource_without_discarding_it(tmp_path: Path):
    candidates, _index, _out_root = build_inputs(tmp_path)
    active, count = round_runner.defer_high_resource_candidates(
        candidates, tmp_path / "active.csv", tmp_path / "deferred.csv")
    assert active == tmp_path / "active.csv"
    assert count == 1
    assert "huge" not in {row["design"] for row in csv.DictReader(active.open())}
    assert {row["design"] for row in csv.DictReader((tmp_path / "deferred.csv").open())} == {"huge"}
