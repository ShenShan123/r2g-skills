import importlib.util
from pathlib import Path

import torch


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/experiment4_contract.py"
SPEC = importlib.util.spec_from_file_location("experiment4_contract", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_config_contract_publishes_exact_yosys_key_without_value():
    contract = MODULE._config_contract({"schema": "r2g2_four_stage_sample_v1", "yosys_v": "/secret/a.v"})
    assert "yosys_v" in contract["fields"]
    assert "graph_id" not in contract["fields"]
    assert "/secret/a.v" not in str(contract)


def test_lint_reports_all_missing_products_in_one_pass(tmp_path):
    contract = {
        "required_converter_products": ["a.csv", "nested/b.json", "graph.pt"],
        "csv_schemas": {"a.csv": {"header": ["graph_id"]}},
        "graph_schemas": {},
    }
    report = MODULE.lint_output(contract, tmp_path)
    assert report["status"] == "FAIL"
    assert report["checks_total"] == 4
    assert report["issue_count"] == 4
    assert {item["check"] for item in report["issues"]} == {
        "file:a.csv", "file:nested/b.json", "file:graph.pt", "csv_header:a.csv"
    }


def test_edge_index_allows_design_dependent_edge_count():
    spec = {"kind": "tensor", "dtype": "torch.int64", "rank": 2, "trailing_shape": [3]}
    assert MODULE._tensor_shape_matches("edge_index", torch.zeros((2, 19), dtype=torch.int64), spec)
    assert not MODULE._tensor_shape_matches("edge_index", torch.zeros((3, 19), dtype=torch.int64), spec)


def test_non_edge_tensor_still_requires_frozen_feature_width():
    spec = {"kind": "tensor", "dtype": "torch.float32", "rank": 2, "trailing_shape": [3]}
    assert MODULE._tensor_shape_matches("x", torch.zeros((19, 3)), spec)
    assert not MODULE._tensor_shape_matches("x", torch.zeros((19, 4)), spec)


def test_io_pin_direction_sequence_is_design_dependent_but_typed():
    assert MODULE._required_value_matches("io_pin_directions", ["OUTPUT"], ["INPUT", "OUTPUT"])
    assert not MODULE._required_value_matches("io_pin_directions", [1], ["INPUT", "OUTPUT"])
    assert not MODULE._required_value_matches("x_schema", ["b"], ["a"])


def test_optional_edge_types_may_extend_required_public_canary_relations():
    required = [["gate", "has", "pin"]]
    actual = [["gate", "has", "pin"], ["io_pin", "rc_resistance", "pin"]]
    assert MODULE._edge_types_match(actual, required)
    assert not MODULE._edge_types_match([], required)
    assert MODULE._required_value_matches(
        "supervision_edge_types",
        ["pin|rc_resistance|pin", "io_pin|rc_resistance|pin"],
        ["pin|rc_resistance|pin"],
    )
