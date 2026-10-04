#!/usr/bin/env python3
"""Build and lint the public, implementation-neutral Experiment 4 contract."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import torch


STAGES = ("floorplan", "placement", "cts", "route")
EVALUATOR_PRODUCTS = {
    "four_stage.validation.json",
    "statistics/four_stage_data_statistics.csv",
    "statistics/four_stage_data_statistics.json",
    "statistics/four_stage_data_statistics.md",
}
IDENTITY_OR_PATH_TOKENS = ("design", "name", "path", "file", "sha256", "provenance", "sample", "manifest")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _json_shape(value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        if len(value) > 64:
            return {"type": "object", "keys": "dynamic_mapping", "minimum_property_count": 1}
        return {"type": "object", "properties": {key: _json_shape(item, depth + 1) for key, item in sorted(value.items())}}
    if isinstance(value, list):
        return {"type": "array", "item": _json_shape(value[0], depth + 1) if value else {"type": "unknown"}}
    if value is None:
        return {"type": "null"}
    return {"type": type(value).__name__}


def _tensor_spec(value: torch.Tensor) -> dict[str, Any]:
    return {
        "kind": "tensor",
        "dtype": str(value.dtype),
        "rank": value.ndim,
        "trailing_shape": list(value.shape[1:]),
    }


def _attribute_spec(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, torch.Tensor):
        return _tensor_spec(value)
    spec: dict[str, Any] = {"kind": type(value).__name__}
    lower = key.lower()
    may_publish_value = not any(token in lower for token in IDENTITY_OR_PATH_TOKENS)
    if may_publish_value and isinstance(value, (str, int, float, bool, type(None))):
        if not isinstance(value, str) or (not value.startswith("/") and "exp1_" not in value):
            spec["required_value"] = value
    elif may_publish_value and isinstance(value, (list, tuple)) and len(value) <= 128 and all(
        isinstance(item, (str, int, float, bool, type(None))) for item in value
    ):
        spec["required_value"] = list(value)
    elif may_publish_value and isinstance(value, dict) and any(
        token in lower for token in ("contract", "policy", "semantics")
    ):
        try:
            encoded = json.dumps(value)
        except TypeError:
            pass
        else:
            if len(encoded) <= 4096 and "/home/" not in encoded and "exp1_" not in encoded:
                spec["required_value"] = value
    return spec


def _store_spec(store: Any) -> dict[str, Any]:
    return {key: _attribute_spec(key, store[key]) for key in sorted(store.keys())}


def _graph_spec(path: Path) -> dict[str, Any]:
    graph = torch.load(path, map_location="cpu", weights_only=False)
    if hasattr(graph, "node_types"):
        return {
            "kind": "HeteroData",
            "global_attributes": _store_spec(graph._global_store),
            "node_types": list(graph.node_types),
            "edge_types": [list(edge_type) for edge_type in graph.edge_types],
            "node_stores": {node_type: _store_spec(graph[node_type]) for node_type in graph.node_types},
            "edge_stores": {"|".join(edge_type): _store_spec(graph[edge_type]) for edge_type in graph.edge_types},
        }
    return {"kind": type(graph).__name__, "attributes": _store_spec(graph)}


def _config_contract(config: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in sorted(config.items()):
        if isinstance(value, list):
            value_type = "list[string]" if all(isinstance(item, str) for item in value) else "list"
        elif isinstance(value, bool):
            value_type = "boolean"
        elif isinstance(value, (int, float)):
            value_type = "number"
        elif value is None:
            value_type = "null"
        else:
            value_type = "string"
        fields[key] = {
            "type": value_type,
            "path": key in {
                "config_mk", "cts_def", "encode_map", "floorplan_def", "label_def", "output_dir",
                "place_def", "raw_manifest", "route_def", "sdc", "spef", "timing_manifest",
                "timing_max_rpt", "timing_min_rpt", "yosys_v",
            } or key in {"lef", "lib"},
        }
    return {
        "schema": config.get("schema"),
        "fields": fields,
        "path_resolution": "All supplied paths are absolute; write products only below output_dir.",
        "input_aliases_are_forbidden": "Use the exact field names above; in particular the synthesized netlist field is yosys_v.",
    }


def build_contract(config_path: Path, reference_root: Path) -> dict[str, Any]:
    config = read_json(config_path)
    files = sorted(
        str(path.relative_to(reference_root))
        for path in reference_root.rglob("*")
        if path.is_file() and str(path.relative_to(reference_root)) not in EVALUATOR_PRODUCTS
    )
    csv_schemas: dict[str, Any] = {}
    json_schemas: dict[str, Any] = {}
    graph_schemas: dict[str, Any] = {}
    for relative in files:
        path = reference_root / relative
        if path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                csv_schemas[relative] = {"header": next(csv.reader(handle), [])}
        elif path.suffix == ".json":
            json_schemas[relative] = _json_shape(read_json(path))
        elif path.suffix == ".pt":
            graph_schemas[relative] = _graph_spec(path)
    return {
        "schema_version": "experiment4-public-contract-2.0",
        "created_at": utc_now(),
        "provenance": "Schema-only extraction from the public canary output; no task identity, path, row value, tensor value, or hidden-test data is included.",
        "target_contract": "r2g2_four_stage_hetero_pipeline_v3",
        "stages": list(STAGES),
        "method_config": _config_contract(config),
        "required_converter_products": files,
        "csv_schemas": csv_schemas,
        "json_shapes": json_schemas,
        "graph_schemas": graph_schemas,
    }


def _group_equal_schemas(schemas: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for path, schema in sorted(schemas.items()):
        encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        groups.setdefault(digest, {"paths": [], "schema": schema})["paths"].append(path)
    return list(groups.values())


def _merge_store_specs(stores_by_stage: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    keys = sorted({key for store in stores_by_stage.values() for key in store})
    for key in keys:
        variants = {stage: store.get(key) for stage, store in stores_by_stage.items()}
        encoded = {json.dumps(value, sort_keys=True, separators=(",", ":")) for value in variants.values()}
        if len(encoded) == 1:
            merged[key] = next(iter(variants.values()))
        else:
            merged[key] = {"by_stage": variants}
    return merged


def model_contract_view(contract: dict[str, Any]) -> dict[str, Any]:
    """Compact the complete linter contract without removing interface facts."""
    graph_schemas = contract["graph_schemas"]
    base_graphs = {path: spec for path, spec in graph_schemas.items() if spec["kind"] != "HeteroData"}
    heterographs = {
        Path(path).parts[1]: spec
        for path, spec in graph_schemas.items()
        if spec["kind"] == "HeteroData" and len(Path(path).parts) > 1
    }
    heterograph_contract: dict[str, Any] = {}
    if heterographs:
        first = next(iter(heterographs.values()))
        node_types_by_stage = {stage: spec["node_types"] for stage, spec in heterographs.items()}
        if len({json.dumps(value) for value in node_types_by_stage.values()}) == 1:
            heterograph_contract["node_types"] = first["node_types"]
        else:
            heterograph_contract["node_types_by_stage"] = node_types_by_stage
        heterograph_contract["edge_types_by_stage"] = {
            stage: spec["edge_types"] for stage, spec in heterographs.items()
        }
        heterograph_contract["global_attributes"] = _merge_store_specs(
            {stage: spec["global_attributes"] for stage, spec in heterographs.items()}
        )
        all_node_types = sorted({node for spec in heterographs.values() for node in spec["node_stores"]})
        heterograph_contract["node_stores"] = {
            node: _merge_store_specs(
                {stage: spec["node_stores"][node] for stage, spec in heterographs.items() if node in spec["node_stores"]}
            )
            for node in all_node_types
        }
        all_edges = sorted({edge for spec in heterographs.values() for edge in spec["edge_stores"]})
        heterograph_contract["edge_stores"] = {
            edge: {
                "present_in_stages": [stage for stage, spec in heterographs.items() if edge in spec["edge_stores"]],
                "attributes": _merge_store_specs(
                    {stage: spec["edge_stores"][edge] for stage, spec in heterographs.items() if edge in spec["edge_stores"]}
                ),
            }
            for edge in all_edges
        }
    compact_json_shapes = {
        path: {
            "type": schema.get("type"),
            "required_top_level_keys": sorted((schema.get("properties") or {}).keys()),
        }
        for path, schema in contract["json_shapes"].items()
    }
    return {
        "schema_version": "experiment4-public-model-contract-2.0",
        "provenance": contract["provenance"],
        "target_contract": contract["target_contract"],
        "stages": contract["stages"],
        "method_config": contract["method_config"],
        "required_converter_products": contract["required_converter_products"],
        "csv_schema_groups": _group_equal_schemas(contract["csv_schemas"]),
        "json_top_level_schema_groups": _group_equal_schemas(compact_json_shapes),
        "base_graphs": base_graphs,
        "heterograph": heterograph_contract,
    }


def _same_required_value(actual: Any, required: Any) -> bool:
    if isinstance(actual, tuple):
        actual = list(actual)
    return actual == required


def _tensor_shape_matches(key: str, value: torch.Tensor, spec: dict[str, Any]) -> bool:
    if value.ndim != spec["rank"]:
        return False
    # edge_index is conventionally [2, E]; E is design-dependent, not part of the interface.
    if key == "edge_index":
        return value.ndim == 2 and value.shape[0] == 2
    return list(value.shape[1:]) == spec["trailing_shape"]


def _required_value_matches(key: str, actual: Any, required: Any) -> bool:
    # This is row data whose sequence and length naturally vary with each design.
    if key == "io_pin_directions":
        return isinstance(actual, (list, tuple)) and all(isinstance(item, str) for item in actual)
    if key.endswith("_edge_types") and isinstance(actual, (list, tuple)) and isinstance(required, (list, tuple)):
        return set(required).issubset(set(actual))
    return _same_required_value(actual, required)


def _edge_types_match(actual: list[list[str]], required: list[list[str]]) -> bool:
    return {tuple(edge_type) for edge_type in required}.issubset(
        {tuple(edge_type) for edge_type in actual}
    )


def _lint_store(store: Any, expected: dict[str, Any], prefix: str, checks: list[dict[str, Any]]) -> None:
    actual_keys = set(store.keys())
    for key, spec in expected.items():
        present = key in actual_keys
        checks.append({"check": f"{prefix}:attribute:{key}", "passed": present, "detail": "present" if present else "missing"})
        if not present:
            continue
        value = store[key]
        if spec.get("kind") == "tensor":
            tensor_ok = isinstance(value, torch.Tensor)
            checks.append({"check": f"{prefix}:tensor:{key}", "passed": tensor_ok, "detail": type(value).__name__})
            if tensor_ok:
                shape_ok = _tensor_shape_matches(key, value, spec)
                dtype_ok = str(value.dtype) == spec["dtype"]
                checks.append({"check": f"{prefix}:shape:{key}", "passed": shape_ok, "detail": str(list(value.shape))})
                checks.append({"check": f"{prefix}:dtype:{key}", "passed": dtype_ok, "detail": str(value.dtype)})
        elif "required_value" in spec:
            value_ok = _required_value_matches(key, value, spec["required_value"])
            detail = "semantic match" if value_ok else "mismatch"
            checks.append({"check": f"{prefix}:value:{key}", "passed": value_ok, "detail": detail})


def _lint_missing_store(expected: dict[str, Any], prefix: str, checks: list[dict[str, Any]]) -> None:
    for key, spec in expected.items():
        checks.append({"check": f"{prefix}:attribute:{key}", "passed": False, "detail": "missing store or attribute"})
        if spec.get("kind") == "tensor":
            for dimension in ("tensor", "shape", "dtype"):
                checks.append({"check": f"{prefix}:{dimension}:{key}", "passed": False, "detail": "missing store or attribute"})
        elif "required_value" in spec:
            checks.append({"check": f"{prefix}:value:{key}", "passed": False, "detail": "missing store or attribute"})


def _lint_missing_graph(relative: str, expected: dict[str, Any], checks: list[dict[str, Any]], detail: str) -> None:
    checks.append({"check": f"graph_load:{relative}", "passed": False, "detail": detail})
    checks.append({"check": f"graph_kind:{relative}", "passed": False, "detail": detail})
    if expected["kind"] == "HeteroData":
        checks.append({"check": f"node_types:{relative}", "passed": False, "detail": detail})
        checks.append({"check": f"edge_types:{relative}", "passed": False, "detail": detail})
        _lint_missing_store(expected["global_attributes"], f"{relative}:global", checks)
        for node_type, store_spec in expected["node_stores"].items():
            _lint_missing_store(store_spec, f"{relative}:node:{node_type}", checks)
        for edge_name, store_spec in expected["edge_stores"].items():
            _lint_missing_store(store_spec, f"{relative}:edge:{edge_name}", checks)
    else:
        _lint_missing_store(expected["attributes"], f"{relative}:data", checks)


def _expected_store_check_names(expected: dict[str, Any], prefix: str) -> list[str]:
    names: list[str] = []
    for key, spec in expected.items():
        names.append(f"{prefix}:attribute:{key}")
        if spec.get("kind") == "tensor":
            names.extend(f"{prefix}:{dimension}:{key}" for dimension in ("tensor", "shape", "dtype"))
        elif "required_value" in spec:
            names.append(f"{prefix}:value:{key}")
    return names


def _expected_check_names(contract: dict[str, Any]) -> list[str]:
    names = [f"file:{relative}" for relative in contract["required_converter_products"]]
    names.extend(f"csv_header:{relative}" for relative in contract["csv_schemas"])
    for relative, expected in contract["graph_schemas"].items():
        names.extend((f"graph_load:{relative}", f"graph_kind:{relative}"))
        if expected["kind"] == "HeteroData":
            names.extend((f"node_types:{relative}", f"edge_types:{relative}"))
            names.extend(_expected_store_check_names(expected["global_attributes"], f"{relative}:global"))
            for node_type, store_spec in expected["node_stores"].items():
                names.extend(_expected_store_check_names(store_spec, f"{relative}:node:{node_type}"))
            for edge_name, store_spec in expected["edge_stores"].items():
                names.extend(_expected_store_check_names(store_spec, f"{relative}:edge:{edge_name}"))
        else:
            names.extend(_expected_store_check_names(expected["attributes"], f"{relative}:data"))
    return names


def lint_output(contract: dict[str, Any], output_root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for relative in contract["required_converter_products"]:
        present = (output_root / relative).is_file()
        checks.append({"check": f"file:{relative}", "passed": present, "detail": "present" if present else "missing"})
    for relative, schema in contract["csv_schemas"].items():
        path = output_root / relative
        if not path.is_file():
            checks.append({"check": f"csv_header:{relative}", "passed": False, "detail": "missing file"})
            continue
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                actual = next(csv.reader(handle), [])
            passed = actual == schema["header"]
            detail = "exact header" if passed else f"expected {schema['header']}; got {actual}"
        except (OSError, UnicodeError, csv.Error) as exc:
            passed, detail = False, f"unreadable CSV: {type(exc).__name__}: {exc}"
        checks.append({"check": f"csv_header:{relative}", "passed": passed, "detail": detail})
    for relative, expected in contract["graph_schemas"].items():
        path = output_root / relative
        if not path.is_file():
            _lint_missing_graph(relative, expected, checks, "missing file")
            continue
        try:
            graph = torch.load(path, map_location="cpu", weights_only=False)
            checks.append({"check": f"graph_load:{relative}", "passed": True, "detail": "loaded"})
            kind_ok = type(graph).__name__ == expected["kind"]
            checks.append({"check": f"graph_kind:{relative}", "passed": kind_ok, "detail": type(graph).__name__})
            if expected["kind"] == "HeteroData" and hasattr(graph, "node_types"):
                node_types_ok = list(graph.node_types) == expected["node_types"]
                edge_types = [list(edge_type) for edge_type in graph.edge_types]
                edge_types_ok = _edge_types_match(edge_types, expected["edge_types"])
                checks.append({"check": f"node_types:{relative}", "passed": node_types_ok, "detail": str(list(graph.node_types))})
                checks.append({"check": f"edge_types:{relative}", "passed": edge_types_ok, "detail": str(edge_types)})
                _lint_store(graph._global_store, expected["global_attributes"], f"{relative}:global", checks)
                for node_type, store_spec in expected["node_stores"].items():
                    if node_type in graph.node_types:
                        _lint_store(graph[node_type], store_spec, f"{relative}:node:{node_type}", checks)
                    else:
                        _lint_missing_store(store_spec, f"{relative}:node:{node_type}", checks)
                for edge_name, store_spec in expected["edge_stores"].items():
                    edge_type = tuple(edge_name.split("|"))
                    if edge_type in graph.edge_types:
                        _lint_store(graph[edge_type], store_spec, f"{relative}:edge:{edge_name}", checks)
                    else:
                        _lint_missing_store(store_spec, f"{relative}:edge:{edge_name}", checks)
            elif expected["kind"] == "HeteroData":
                checks.append({"check": f"node_types:{relative}", "passed": False, "detail": "not HeteroData"})
                checks.append({"check": f"edge_types:{relative}", "passed": False, "detail": "not HeteroData"})
                _lint_missing_store(expected["global_attributes"], f"{relative}:global", checks)
                for node_type, store_spec in expected["node_stores"].items():
                    _lint_missing_store(store_spec, f"{relative}:node:{node_type}", checks)
                for edge_name, store_spec in expected["edge_stores"].items():
                    _lint_missing_store(store_spec, f"{relative}:edge:{edge_name}", checks)
            elif expected["kind"] != "HeteroData":
                _lint_store(graph, expected["attributes"], f"{relative}:data", checks)
        except Exception as exc:
            _lint_missing_graph(relative, expected, checks, f"{type(exc).__name__}: {exc}")
    observed = {item["check"]: item for item in checks}
    checks = [
        observed.get(name, {"check": name, "passed": False, "detail": "check could not be evaluated"})
        for name in _expected_check_names(contract)
    ]
    passed = sum(item["passed"] is True for item in checks)
    total = len(checks)
    issues = [item for item in checks if not item["passed"]]
    return {
        "schema_version": "experiment4-contract-lint-2.0",
        "created_at": utc_now(),
        "status": "PASS" if total and passed == total else "FAIL",
        "checks_passed": passed,
        "checks_total": total,
        "fraction": round(passed / total, 6) if total else 0.0,
        "issue_count": len(issues),
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--config", type=Path, required=True)
    build.add_argument("--reference-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--model-output", type=Path)
    lint = subparsers.add_parser("lint")
    lint.add_argument("--contract", type=Path, required=True)
    lint.add_argument("--output-root", type=Path, required=True)
    lint.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        contract = build_contract(args.config, args.reference_root)
        write_json(args.output, contract)
        if args.model_output:
            write_json(args.model_output, model_contract_view(contract))
    else:
        write_json(args.output, lint_output(read_json(args.contract), args.output_root))


if __name__ == "__main__":
    main()
