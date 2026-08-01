#!/usr/bin/env python3
"""统计四阶段异构图中全部节点/边特征、标签及其完整性。

输出三种互补格式：

* ``four_stage_data_statistics.json``：完整嵌套报告，适合程序检查；
* ``four_stage_data_statistics.csv``：每个特征/标签一行，适合筛选排序；
* ``four_stage_data_statistics.md``：人工检查摘要和缺失率明细。

NaN并不自动视为错误，因为早期阶段缺少物理特征、部分监督任务没有覆盖所有实体
都是数据契约允许的情况。脚本会把这些缺失完整列出；只有schema、mask一致性、边索引
越界或四阶段共享标签/拓扑不一致才记为结构错误。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import torch


STAGES = ("floorplan", "placement", "cts", "route")
STAGE_SPECIFIC_EDGE_TYPES = {("gate", "congestion_geom", "gate")}
CSV_COLUMNS = [
    "stage",
    "scope",
    "entity_type",
    "tensor",
    "column_index",
    "column",
    "unit",
    "total_count",
    "finite_count",
    "finite_percent",
    "missing_count",
    "nan_count",
    "positive_inf_count",
    "negative_inf_count",
    "zero_count",
    "unique_finite_count",
    "minimum",
    "maximum",
    "mean",
    "std_population",
    "mask_available",
    "mask_valid_count",
    "mask_valid_percent",
    "valid_but_nonfinite_count",
    "invalid_but_finite_count",
]


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def edge_name(edge_type: tuple[str, str, str]) -> str:
    return "|".join(edge_type)


def tensor_sha256(tensor: torch.Tensor) -> str:
    value = tensor.detach().cpu().contiguous()
    digest = hashlib.sha256()
    digest.update(str(value.dtype).encode("utf-8"))
    digest.update(str(tuple(value.shape)).encode("utf-8"))
    digest.update(value.numpy().tobytes())
    return digest.hexdigest()


def normalized_schema(
    schema: Any, width: int, tensor_name: str
) -> list[str]:
    names = [str(value) for value in list(schema or [])]
    if not names and width:
        names = [f"{tensor_name}_{index}" for index in range(width)]
    if len(names) != width:
        raise ValueError(
            f"{tensor_name} schema宽度不一致: schema={len(names)} tensor={width}"
        )
    return names


def normalized_units(units: Any, width: int) -> list[str]:
    values = [str(value) for value in list(units or [])]
    return values if len(values) == width else [""] * width


def as_matrix(tensor: torch.Tensor, schema_width: int) -> torch.Tensor:
    value = tensor.detach().cpu()
    if value.ndim == 2:
        return value
    if value.ndim == 1:
        if schema_width == 1:
            return value.reshape(-1, 1)
        if value.numel() == schema_width:
            return value.reshape(1, -1)
    if value.ndim == 0 and schema_width == 1:
        return value.reshape(1, 1)
    raise ValueError(
        f"不能把shape={tuple(value.shape)}解释为{schema_width}列矩阵"
    )


def scalar_or_none(value: torch.Tensor, operation: str) -> float | None:
    if value.numel() == 0:
        return None
    result = getattr(value, operation)().item()
    return float(result) if math.isfinite(float(result)) else None


def summarize_tensor(
    *,
    stage: str,
    scope: str,
    entity_type: str,
    tensor_name: str,
    tensor: torch.Tensor,
    schema: Any,
    units: Any = None,
    valid_mask: torch.Tensor | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    width = int(tensor.shape[-1]) if tensor.ndim >= 2 else len(list(schema or []))
    names = normalized_schema(schema, width, tensor_name)
    unit_names = normalized_units(units, width)
    matrix = as_matrix(tensor, width)
    mask_matrix: torch.Tensor | None = None
    issues: list[str] = []
    if valid_mask is not None:
        mask_matrix = as_matrix(valid_mask.to(dtype=torch.bool), width)
        if mask_matrix.shape != matrix.shape:
            issues.append(
                f"{stage}/{entity_type}/{tensor_name}: mask shape "
                f"{tuple(mask_matrix.shape)} != {tuple(matrix.shape)}"
            )
            mask_matrix = None

    rows: list[dict[str, Any]] = []
    for index, (name, unit) in enumerate(zip(names, unit_names)):
        raw = matrix[:, index]
        numeric = raw.to(dtype=torch.float64)
        finite_mask = torch.isfinite(numeric)
        nan_count = int(torch.isnan(numeric).sum())
        positive_inf_count = int(torch.isposinf(numeric).sum())
        negative_inf_count = int(torch.isneginf(numeric).sum())
        finite = numeric[finite_mask]
        total = int(numeric.numel())
        finite_count = int(finite.numel())
        row: dict[str, Any] = {
            "stage": stage,
            "scope": scope,
            "entity_type": entity_type,
            "tensor": tensor_name,
            "column_index": index,
            "column": name,
            "unit": unit,
            "total_count": total,
            "finite_count": finite_count,
            "finite_percent": 100.0 * finite_count / total if total else 0.0,
            "missing_count": total - finite_count,
            "nan_count": nan_count,
            "positive_inf_count": positive_inf_count,
            "negative_inf_count": negative_inf_count,
            "zero_count": int((finite == 0).sum()),
            "unique_finite_count": int(torch.unique(finite).numel()),
            "minimum": scalar_or_none(finite, "min"),
            "maximum": scalar_or_none(finite, "max"),
            "mean": scalar_or_none(finite, "mean"),
            "std_population": (
                float(finite.std(unbiased=False).item()) if finite_count else None
            ),
            "mask_available": int(mask_matrix is not None),
            "mask_valid_count": "",
            "mask_valid_percent": "",
            "valid_but_nonfinite_count": "",
            "invalid_but_finite_count": "",
        }
        if mask_matrix is not None:
            column_mask = mask_matrix[:, index]
            valid_count = int(column_mask.sum())
            valid_but_nonfinite = int((column_mask & ~finite_mask).sum())
            invalid_but_finite = int((~column_mask & finite_mask).sum())
            row.update(
                {
                    "mask_valid_count": valid_count,
                    "mask_valid_percent": (
                        100.0 * valid_count / total if total else 0.0
                    ),
                    "valid_but_nonfinite_count": valid_but_nonfinite,
                    "invalid_but_finite_count": invalid_but_finite,
                }
            )
            if valid_but_nonfinite or invalid_but_finite:
                issues.append(
                    f"{stage}/{entity_type}/{tensor_name}/{name}: "
                    f"valid_nonfinite={valid_but_nonfinite}, "
                    f"invalid_finite={invalid_but_finite}"
                )
        rows.append(row)
    return rows, issues


def summarize_edge_topology(
    graph: Any, edge_type: tuple[str, str, str]
) -> tuple[dict[str, Any], list[str]]:
    source_type, _, target_type = edge_type
    edge_index = graph[edge_type].edge_index.detach().cpu()
    edge_count = int(edge_index.shape[1])
    source_count = int(graph[source_type].num_nodes)
    target_count = int(graph[target_type].num_nodes)
    issues: list[str] = []
    if edge_index.ndim != 2 or edge_index.shape[0] != 2:
        return {
            "edge_count": edge_count,
            "shape": list(edge_index.shape),
        }, [f"{edge_name(edge_type)} edge_index shape非法"]
    if edge_count:
        invalid_source = int(
            ((edge_index[0] < 0) | (edge_index[0] >= source_count)).sum()
        )
        invalid_target = int(
            ((edge_index[1] < 0) | (edge_index[1] >= target_count)).sum()
        )
        unique_count = int(torch.unique(edge_index.t(), dim=0).shape[0])
        source_degree = torch.bincount(edge_index[0], minlength=source_count)
        target_degree = torch.bincount(edge_index[1], minlength=target_count)
    else:
        invalid_source = invalid_target = unique_count = 0
        source_degree = torch.zeros(source_count, dtype=torch.long)
        target_degree = torch.zeros(target_count, dtype=torch.long)
    if invalid_source or invalid_target:
        issues.append(
            f"{edge_name(edge_type)}端点越界: source={invalid_source}, "
            f"target={invalid_target}"
        )
    result = {
        "source_node_type": source_type,
        "target_node_type": target_type,
        "source_node_count": source_count,
        "target_node_count": target_count,
        "edge_count": edge_count,
        "unique_directed_pair_count": unique_count,
        "duplicate_directed_edge_count": edge_count - unique_count,
        "invalid_source_index_count": invalid_source,
        "invalid_target_index_count": invalid_target,
        "self_loop_count": (
            int((edge_index[0] == edge_index[1]).sum())
            if source_type == target_type and edge_count
            else 0
        ),
        "source_nodes_with_edges": int((source_degree > 0).sum()),
        "source_nodes_without_edges": int((source_degree == 0).sum()),
        "target_nodes_with_edges": int((target_degree > 0).sum()),
        "target_nodes_without_edges": int((target_degree == 0).sum()),
        "max_source_out_degree": int(source_degree.max()) if source_count else 0,
        "max_target_in_degree": int(target_degree.max()) if target_count else 0,
    }
    return result, issues


def format_number(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def markdown_report(report: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# 四阶段异构图特征与标签统计",
        "",
        f"- 数据目录：`{report['graph_root']}`",
        f"- 结构检查：**{report['status']}**",
        f"- 统计列数：{len(rows)}",
        f"- 结构问题数：{len(report['structural_issues'])}",
        "",
        "## 四阶段规模",
        "",
        "| 阶段 | 节点数（gate/net/io_pin/pin） | 边类型数 | 总边数 |",
        "|---|---:|---:|---:|",
    ]
    for stage in STAGES:
        value = report["stages"][stage]
        nodes = value["node_counts"]
        node_text = "/".join(
            str(nodes.get(name, 0)) for name in ("gate", "net", "io_pin", "pin")
        )
        total_edges = sum(
            item["edge_count"] for item in value["edge_topology"].values()
        )
        lines.append(
            f"| {stage} | {node_text} | {len(value['edge_topology'])} | "
            f"{total_edges} |"
        )

    missing = [row for row in rows if int(row["missing_count"]) > 0]
    lines.extend(
        [
            "",
            "## 存在缺失值的特征与标签",
            "",
            "NaN可能是符合阶段因果边界的预期缺失；请结合阶段和valid mask判断。",
            "",
            "| 阶段 | 对象 | 张量 | 列 | 有限值/总数 | 完整率 | mask有效数 |",
            "|---|---|---|---|---:|---:|---:|",
        ]
    )
    for row in missing:
        lines.append(
            f"| {row['stage']} | {row['entity_type']} | {row['tensor']} | "
            f"{row['column']} | {row['finite_count']}/{row['total_count']} | "
            f"{format_number(row['finite_percent'])}% | "
            f"{format_number(row['mask_valid_count'])} |"
        )
    if not missing:
        lines.append("| - | - | - | - | - | 100% | - |")

    lines.extend(
        [
            "",
            "## 全部特征与标签明细",
            "",
            "| 阶段 | 范围 | 对象 | 张量 | 列 | 完整率 | min | max | mean | unique |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['stage']} | {row['scope']} | {row['entity_type']} | "
            f"{row['tensor']} | {row['column']} | "
            f"{format_number(row['finite_percent'])}% | "
            f"{format_number(row['minimum'])} | {format_number(row['maximum'])} | "
            f"{format_number(row['mean'])} | {row['unique_finite_count']} |"
        )

    lines.extend(["", "## 结构问题", ""])
    if report["structural_issues"]:
        lines.extend(f"- {issue}" for issue in report["structural_issues"])
    else:
        lines.append("- 未发现schema、mask、边索引或跨阶段共享数据错误。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        required=True,
        help="样本生成目录，例如 generated/bp_multi_top/v01",
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help="报告目录，默认<root>/statistics",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    out_dir = (
        Path(args.out_dir).resolve()
        if args.out_dir
        else root / "statistics"
    )
    report: dict[str, Any] = {
        "schema": "r2g2_four_stage_graph_data_statistics_v1",
        "graph_root": str(root),
        "stages": {},
        "cross_stage_checks": {},
        "structural_issues": [],
    }
    rows: list[dict[str, Any]] = []
    reference: dict[str, Any] = {}

    for stage in STAGES:
        graph_path = root / "stages" / stage / "heterograph.pt"
        if not graph_path.is_file():
            raise FileNotFoundError(
                f"缺少{stage}异构图: {graph_path}；请先运行04_assemble_heterograph.py"
            )
        graph = torch.load(graph_path, map_location="cpu", weights_only=False)
        stage_report: dict[str, Any] = {
            "graph_path": str(graph_path),
            "prediction_stage": str(getattr(graph, "prediction_stage", stage)),
            "feature_cutoff": str(getattr(graph, "feature_cutoff", "")),
            "node_counts": {},
            "edge_topology": {},
            "tensor_hashes": {},
        }

        global_rows, global_issues = summarize_tensor(
            stage=stage,
            scope="graph",
            entity_type="graph",
            tensor_name="global_features",
            tensor=graph.global_features,
            schema=graph.global_feature_schema,
        )
        rows.extend(global_rows)
        report["structural_issues"].extend(global_issues)

        for node_type in graph.node_types:
            store = graph[node_type]
            stage_report["node_counts"][node_type] = int(store.num_nodes)
            x_rows, x_issues = summarize_tensor(
                stage=stage,
                scope="node",
                entity_type=node_type,
                tensor_name="x",
                tensor=store.x,
                schema=getattr(store, "x_schema", []),
            )
            rows.extend(x_rows)
            report["structural_issues"].extend(x_issues)
            stage_report["tensor_hashes"][f"node/{node_type}/x_schema"] = list(
                getattr(store, "x_schema", [])
            )
            if hasattr(store, "y"):
                y_rows, y_issues = summarize_tensor(
                    stage=stage,
                    scope="node",
                    entity_type=node_type,
                    tensor_name="y",
                    tensor=store.y,
                    schema=getattr(store, "y_schema", []),
                    units=getattr(store, "y_unit", []),
                    valid_mask=getattr(store, "y_valid_mask", None),
                )
                rows.extend(y_rows)
                report["structural_issues"].extend(y_issues)
                stage_report["tensor_hashes"][f"node/{node_type}/y"] = (
                    tensor_sha256(store.y)
                )
                stage_report["tensor_hashes"][f"node/{node_type}/y_mask"] = (
                    tensor_sha256(store.y_valid_mask)
                )

        for edge_type in graph.edge_types:
            store = graph[edge_type]
            name = edge_name(edge_type)
            topology, topology_issues = summarize_edge_topology(graph, edge_type)
            stage_report["edge_topology"][name] = topology
            report["structural_issues"].extend(
                f"{stage}/{issue}" for issue in topology_issues
            )
            stage_report["tensor_hashes"][f"edge/{name}/index"] = tensor_sha256(
                store.edge_index
            )
            if hasattr(store, "edge_attr"):
                attr_rows, attr_issues = summarize_tensor(
                    stage=stage,
                    scope="edge",
                    entity_type=name,
                    tensor_name="edge_attr",
                    tensor=store.edge_attr,
                    schema=getattr(store, "edge_schema", []),
                )
                rows.extend(attr_rows)
                report["structural_issues"].extend(attr_issues)
            if hasattr(store, "edge_y"):
                edge_y_rows, edge_y_issues = summarize_tensor(
                    stage=stage,
                    scope="edge",
                    entity_type=name,
                    tensor_name="edge_y",
                    tensor=store.edge_y,
                    schema=getattr(store, "edge_y_schema", []),
                    units=getattr(store, "edge_y_unit", []),
                    valid_mask=getattr(store, "edge_y_mask", None),
                )
                rows.extend(edge_y_rows)
                report["structural_issues"].extend(edge_y_issues)
                stage_report["tensor_hashes"][f"edge/{name}/y"] = tensor_sha256(
                    store.edge_y
                )
                stage_report["tensor_hashes"][f"edge/{name}/y_mask"] = (
                    tensor_sha256(store.edge_y_mask)
                )

        report["stages"][stage] = stage_report
        if stage == STAGES[0]:
            reference = stage_report
        else:
            if stage_report["node_counts"] != reference["node_counts"]:
                report["structural_issues"].append(
                    f"{stage}: 节点数量与floorplan不一致"
                )
            for key, digest in reference["tensor_hashes"].items():
                if key.endswith("/x_schema") or "/y" in key:
                    if stage_report["tensor_hashes"].get(key) != digest:
                        report["structural_issues"].append(
                            f"{stage}: 共享schema/标签不一致: {key}"
                        )
            reference_edges = reference["edge_topology"]
            current_edges = stage_report["edge_topology"]
            reference_shared_edges = {
                name
                for name in reference_edges
                if tuple(name.split("|")) not in STAGE_SPECIFIC_EDGE_TYPES
            }
            current_shared_edges = {
                name
                for name in current_edges
                if tuple(name.split("|")) not in STAGE_SPECIFIC_EDGE_TYPES
            }
            if reference_shared_edges != current_shared_edges:
                report["structural_issues"].append(
                    f"{stage}: 共享边类型集合与floorplan不一致"
                )
            for name in set(reference_edges) & set(current_edges):
                edge_type = tuple(name.split("|"))
                if edge_type in STAGE_SPECIFIC_EDGE_TYPES:
                    continue
                key = f"edge/{name}/index"
                if stage_report["tensor_hashes"].get(key) != reference[
                    "tensor_hashes"
                ].get(key):
                    report["structural_issues"].append(
                        f"{stage}: 共享边索引与floorplan不一致: {name}"
                    )

        del graph

    report["cross_stage_checks"] = {
        "node_counts_equal": not any(
            "节点数量与floorplan不一致" in issue
            for issue in report["structural_issues"]
        ),
        "node_and_edge_labels_equal": not any(
            "共享schema/标签不一致" in issue
            for issue in report["structural_issues"]
        ),
        "shared_edge_indices_equal": not any(
            "共享边索引与floorplan不一致" in issue
            for issue in report["structural_issues"]
        ),
    }
    report["status"] = "PASS" if not report["structural_issues"] else "FAIL"
    report["column_statistics"] = rows

    json_path = out_dir / "four_stage_data_statistics.json"
    csv_path = out_dir / "four_stage_data_statistics.csv"
    markdown_path = out_dir / "four_stage_data_statistics.md"
    atomic_text(
        json_path,
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    atomic_csv(csv_path, rows)
    atomic_text(markdown_path, markdown_report(report, rows))
    print(f"[statistics] status={report['status']}")
    print(f"[statistics] JSON: {json_path}")
    print(f"[statistics] CSV:  {csv_path}")
    print(f"[statistics] MD:   {markdown_path}")
    print(
        f"[statistics] columns={len(rows)} "
        f"structural_issues={len(report['structural_issues'])}"
    )
    if report["structural_issues"]:
        for issue in report["structural_issues"][:20]:
            print(f"[statistics][issue] {issue}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
