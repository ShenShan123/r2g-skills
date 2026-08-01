#!/usr/bin/env python3
"""验证四阶段CSV、共享拓扑、共享标签及feature来源边界。"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import torch


STAGES = ("floorplan", "placement", "cts", "route")
SNAPSHOT_CUTOFFS = {
    "floorplan": "post_yosys",
    "placement": "post_floorplan",
    "cts": "post_placement",
    "route": "post_cts",
}
TABLE_KEYS = {
    "metadata.csv": ("graph_id",),
    "nodes_gate.csv": ("inst_name",),
    "nodes_net.csv": ("net_name",),
    "nodes_iopin.csv": ("iopin_name",),
    "nodes_pin.csv": ("inst_name", "pin_name"),
    "edges_gate_pin.csv": ("inst_name", "pin_name"),
    "edges_pin_net.csv": ("inst_name", "pin_name", "net_name"),
    "edges_iopin_net.csv": ("iopin_name", "net_name"),
}
LOGICAL_EDGE_TYPES = (
    ("gate", "has", "pin"),
    ("pin", "connects_to", "net"),
    ("io_pin", "connects_to", "net"),
)
STAGE_SPECIFIC_EDGE_TYPES = (("gate", "congestion_geom", "gate"),)
LABEL_NODE_TYPES = ("gate", "net", "pin", "io_pin")
AUXILIARY_RELATIONS = {
    "congestion_geom",
    "timing_path",
    "rc_coupling",
    "rc_resistance",
}
SUPERVISION_RELATIONS = {"timing_path", "rc_coupling", "rc_resistance"}
AUXILIARY_LABEL_COLUMNS = {
    "setup_delay_ns",
    "hold_delay_ns",
    "effective_resistance_ohm",
    "coupling_cap_pF",
}


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def keys(rows: list[dict[str, str]], columns: tuple[str, ...]) -> list[Any]:
    values = [tuple(row[column] for column in columns) for row in rows]
    return [value[0] if len(value) == 1 else value for value in values]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def same_tensor(left: torch.Tensor, right: torch.Tensor) -> bool:
    return left.shape == right.shape and torch.equal(left, right)


def same_float_tensor(left: torch.Tensor, right: torch.Tensor) -> bool:
    return left.shape == right.shape and torch.allclose(
        left, right, equal_nan=True
    )


def _resolve_expected_grid_um(cfg: dict[str, Any]) -> float:
    """Same congestion-grid resolution stage 02 and 04 perform."""

    spec = importlib.util.spec_from_file_location(
        "r2g2_features_for_validate",
        Path(__file__).resolve().parent.parent / "02_extract_features.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError("02_extract_features.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return float(module.resolve_congestion_grid_um(cfg))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    # r2g-skills delta vs upstream R2G2.0 (D3): the checker hardcoded 2.1um/4200
    # DBU, i.e. Nangate45's 15 x Metal3 pitch. The invariant that actually
    # matters is "one fixed, pre-route-knowable grid, identical in the features,
    # the labels and the Gate-Gate relation" -- not that literal number. Resolve
    # the platform's value from the same helper stage 02/04 use and check
    # consistency against it.
    expected_grid_um = _resolve_expected_grid_um(cfg)
    output = Path(cfg["output_dir"])
    if not output.is_absolute():
        output = (config_path.parent / output).resolve()

    reference_headers: dict[str, list[str]] = {}
    reference_keys: dict[str, list[Any]] = {}
    stage_stats: dict[str, Any] = {}
    route_label_path = Path(str(cfg.get("label_def") or cfg.get("route_def"))).resolve()

    for stage in STAGES:
        feature_dir = output / "stages" / stage / "features"
        per_stage: dict[str, Any] = {}
        for filename, key_columns in TABLE_KEYS.items():
            header, rows = read_csv(feature_dir / filename)
            row_keys = keys(rows, key_columns)
            if len(row_keys) != len(set(row_keys)):
                raise ValueError(f"{stage}/{filename}存在重复实体键")
            if stage == STAGES[0]:
                reference_headers[filename] = header
                reference_keys[filename] = row_keys
            else:
                if header != reference_headers[filename]:
                    raise ValueError(f"{stage}/{filename}表头与floorplan不一致")
                if row_keys != reference_keys[filename]:
                    raise ValueError(f"{stage}/{filename}实体键或顺序不一致")
            per_stage[filename] = len(rows)

        _, metadata_rows = read_csv(feature_dir / "metadata.csv")
        metadata = metadata_rows[0]
        if metadata.get("prediction_stage") != stage:
            raise ValueError(f"{stage} metadata prediction_stage错误")
        feature_source = metadata.get("feature_source_path", "")
        if feature_source and Path(feature_source).resolve() == route_label_path:
            raise ValueError(f"{stage} feature读取了Route DEF")
        if stage != "floorplan":
            grid_step = float(metadata["congestion_grid_step_x_um"])
            if not math.isclose(grid_step, expected_grid_um, abs_tol=1e-9):
                raise ValueError(
                    f"{stage}拥塞特征网格不是配置的固定{expected_grid_um:g}um: "
                    f"{grid_step}"
                )
            dbu_per_um = float(metadata["dbu_per_um"])
            expected_dbu = int(round(expected_grid_um * dbu_per_um))
            if int(float(metadata["congestion_grid_step_dbu"])) != expected_dbu:
                raise ValueError(
                    f"{stage}拥塞特征网格DBU步长不是{expected_dbu}: "
                    f"{metadata['congestion_grid_step_dbu']}"
                )
        expected_coordinate_trust = int(stage in {"cts", "route"})
        if int(float(metadata["standard_cell_coordinates_trusted"])) != (
            expected_coordinate_trust
        ):
            raise ValueError(f"{stage}标准单元坐标可信性标记错误")
        if int(float(metadata["gate_geom_edge_eligible"])) != (
            expected_coordinate_trust
        ):
            raise ValueError(f"{stage} Gate-Gate阶段资格标记错误")
        for field in ("hpwl_feature_eligible", "congestion_feature_eligible"):
            if int(float(metadata[field])) != expected_coordinate_trust:
                raise ValueError(f"{stage} {field}阶段资格标记错误")

        _, gate_rows = read_csv(feature_dir / "nodes_gate.csv")
        invalid_nan_pairs = 0
        congestion_columns = (
            "congestion_pin_density",
            "congestion_cell_density",
            "congestion_net_density",
            "congestion_rudy",
            "congestion_rudy_pin",
        )
        for row in gate_rows:
            valid = int(float(row["placement_valid"]))
            values = [float(row[column]) for column in ("x_um", "y_um")]
            if valid == 0 and not all(math.isnan(value) for value in values):
                invalid_nan_pairs += 1
            congestion_valid = int(float(row["congestion_feature_valid"]))
            congestion_values = [float(row[column]) for column in congestion_columns]
            if congestion_valid == 0 and not all(
                math.isnan(value) for value in congestion_values
            ):
                raise ValueError(f"{stage}无效Gate拥塞特征没有写NaN")
            if not expected_coordinate_trust and (
                congestion_valid != 0
                or not all(math.isnan(value) for value in congestion_values)
            ):
                raise ValueError(f"{stage}在Placement完成前出现Gate拥塞输入特征")
        if invalid_nan_pairs:
            raise ValueError(
                f"{stage}有{invalid_nan_pairs}个无效Gate位置没有写NaN"
            )
        _, net_rows = read_csv(feature_dir / "nodes_net.csv")
        hpwl_columns = (
            "net_bbox_width_um",
            "net_bbox_height_um",
            "hpwl_um",
            "stage_segment_total_hpwl_um",
            "stage_segment_max_hpwl_um",
            "stage_segment_mean_hpwl_um",
        )
        for row in net_rows:
            hpwl_valid = int(float(row["hpwl_valid"]))
            segment_valid = int(float(row["stage_segment_hpwl_valid"]))
            hpwl_values = [float(row[column]) for column in hpwl_columns]
            if not expected_coordinate_trust and (
                hpwl_valid != 0
                or segment_valid != 0
                or not all(math.isnan(value) for value in hpwl_values)
            ):
                raise ValueError(f"{stage}在Placement完成前出现HPWL输入特征")
        per_stage["feature_source"] = feature_source or "post_yosys"
        snapshot_path = output / "stages" / stage / "stage_snapshot.json"
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if snapshot.get("schema") != "r2g2_target_stage_snapshot_v1":
            raise ValueError(f"{stage}/stage_snapshot.json schema错误")
        if snapshot.get("prediction_stage") != stage:
            raise ValueError(f"{stage}/stage_snapshot.json阶段错误")
        if snapshot.get("model_input_feature_cutoff") != SNAPSHOT_CUTOFFS[stage]:
            raise ValueError(f"{stage}/stage_snapshot.json特征截止点错误")
        snapshot_feature_policy = snapshot.get(
            "model_input_physical_feature_policy", {}
        )
        expected_physical_feature = stage in {"cts", "route"}
        for field in ("hpwl", "congestion_inputs", "gate_gate_geometry"):
            if bool(snapshot_feature_policy.get(field)) != expected_physical_feature:
                raise ValueError(
                    f"{stage}/stage_snapshot.json {field}阶段策略错误"
                )
        if not bool(snapshot_feature_policy.get("all_route_derived_labels_shared")):
            raise ValueError(f"{stage}/stage_snapshot.json共享标签策略错误")
        if "must_not_be_loaded_as_prediction_input" not in str(
            snapshot.get("snapshot_semantics", "")
        ):
            raise ValueError(f"{stage}/stage_snapshot.json缺少防泄漏声明")
        after_path = Path(snapshot["transition"]["after"]["path"]).resolve()
        expected_after_key = {
            "floorplan": "floorplan_def",
            "placement": "place_def",
            "cts": "cts_def",
            "route": "route_def",
        }[stage]
        expected_after_path = Path(str(cfg[expected_after_key]))
        if not expected_after_path.is_absolute():
            expected_after_path = (
                config_path.parent / expected_after_path
            ).resolve()
        if after_path != expected_after_path:
            raise ValueError(f"{stage}/stage_snapshot.json after来源错误")
        per_stage["snapshot"] = {
            "path": str(snapshot_path),
            "component_delta": snapshot["component_delta"]["summary"],
            "net_lineage": snapshot["net_lineage"]["summary"],
        }
        stage_stats[stage] = per_stage

    direct_snapshot_stats: dict[str, Any] = {}
    for physical_stage in ("placement", "route"):
        snapshot_path = (
            output
            / "snapshots"
            / f"base_to_{physical_stage}_snapshot.json"
        )
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if (
            snapshot.get("schema")
            != "r2g2_base_to_physical_stage_snapshot_v1"
            or snapshot.get("physical_stage") != physical_stage
        ):
            raise ValueError(f"base_to_{physical_stage}_snapshot.json错误")
        lineage_summary = snapshot["net_lineage"]["summary"]
        direct_snapshot_stats[physical_stage] = {
            "path": str(snapshot_path),
            "lineage_summary": lineage_summary,
        }

    graphs = {
        stage: torch.load(
            output / "stages" / stage / "heterograph.pt",
            map_location="cpu",
            weights_only=False,
        )
        for stage in STAGES
    }
    expect_irdrop = bool(cfg.get("irdrop_sp"))
    expect_timing = bool(cfg.get("timing_enabled", False))
    expect_timing_edges = expect_timing and bool(
        cfg.get("timing_use_report_path_edges", False)
    )
    for stage, graph in graphs.items():
        if not bool(
            graph.shared_label_contract[
                "same_values_and_masks_across_all_prediction_stages"
            ]
        ):
            raise ValueError(f"{stage}共享Route标签契约缺失")
        if expect_irdrop:
            ir_index = list(graph["gate"].y_schema).index("ir_drop_mV")
            if int(graph["gate"].y_valid_mask[:, ir_index].sum()) == 0:
                raise ValueError(f"{stage}已配置IR-drop源但没有有效Gate标签")
        if expect_timing:
            for node_type in ("pin", "io_pin"):
                valid_count = int(graph[node_type].y_valid_mask.sum())
                if node_type == "pin" and valid_count == 0:
                    raise ValueError(f"{stage}已配置Timing源但没有有效Pin标签")
        if expect_timing_edges and not any(
            relation == "timing_path" for _, relation, _ in graph.edge_types
        ):
            raise ValueError(f"{stage}已启用Timing边任务但图中没有相应标签边")
        auxiliary_edge_types = {
            edge_type
            for edge_type in graph.edge_types
            if edge_type[1] in AUXILIARY_RELATIONS
        }
        declared_auxiliary = {
            tuple(value.split("|")) for value in graph.auxiliary_edge_types
        }
        if declared_auxiliary != auxiliary_edge_types:
            raise ValueError(f"{stage}辅助边类型契约与实际edge store不一致")
        expected_supervision = {
            edge_type
            for edge_type in auxiliary_edge_types
            if edge_type[1] in SUPERVISION_RELATIONS
        }
        declared_supervision = {
            tuple(value.split("|")) for value in graph.supervision_edge_types
        }
        if declared_supervision != expected_supervision:
            raise ValueError(f"{stage}监督边类型契约错误")
        default_message_edges = {
            tuple(value.split("|"))
            for value in graph.default_message_passing_edge_types
        }
        if default_message_edges != set(LOGICAL_EDGE_TYPES):
            raise ValueError(f"{stage}默认消息边混入了辅助关系")
        for edge_type in graph.edge_types:
            schema = set(getattr(graph[edge_type], "edge_schema", []))
            if edge_type not in auxiliary_edge_types and schema & AUXILIARY_LABEL_COLUMNS:
                raise ValueError(
                    f"{stage}/{edge_type}核心edge_attr混入辅助标签列"
                )
        gate_schema = list(graph["gate"].x_schema)
        x_column = gate_schema.index("x_um")
        y_column = gate_schema.index("y_um")
        valid_column = gate_schema.index("placement_valid")
        invalid = graph["gate"].x[:, valid_column] == 0
        if not torch.all(torch.isnan(graph["gate"].x[invalid, x_column])):
            raise ValueError(f"{stage} graph.gate.x无效x坐标没有保留NaN")
        if not torch.all(torch.isnan(graph["gate"].x[invalid, y_column])):
            raise ValueError(f"{stage} graph.gate.x无效y坐标没有保留NaN")
        sidecar = json.loads(
            (
                output / "stages" / stage / "heterograph.metadata.json"
            ).read_text(encoding="utf-8")
        )
        geom_stats = sidecar["congestion_geom_stats"]
        expected_construction = (
            f"fixed_{expected_grid_um:g}um_same_grid_undirected_degree_capped_nearest"
        )
        if (
            geom_stats["construction"] != expected_construction
            or not math.isclose(
                float(geom_stats["window_width_um"]),
                expected_grid_um,
                abs_tol=1e-9,
            )
        ):
            raise ValueError(
                f"{stage} Gate-Gate边不是固定{expected_grid_um:g}um同网格规则: "
                f"{geom_stats['construction']}"
            )
        geom_edge_type = ("gate", "congestion_geom", "gate")
        expected_enabled = stage in {"cts", "route"}
        if bool(geom_stats["enabled"]) != expected_enabled:
            raise ValueError(f"{stage} Gate-Gate边启用状态错误")
        if expected_enabled and geom_edge_type not in graph.edge_types:
            raise ValueError(f"{stage}具备可信坐标但缺少Gate-Gate边类型")
        if not expected_enabled and geom_edge_type in graph.edge_types:
            raise ValueError(f"{stage}没有可信坐标但仍建立Gate-Gate边类型")
        if int(geom_stats["max_undirected_degree"]) != 5:
            raise ValueError(f"{stage} Gate-Gate最大无向度数配置不是5")
        if expected_enabled:
            edge_index = graph[geom_edge_type].edge_index
            pairs = {
                (int(source), int(target))
                for source, target in edge_index.t().tolist()
            }
            if any(source == target for source, target in pairs):
                raise ValueError(f"{stage} Gate-Gate边包含自环")
            if any((target, source) not in pairs for source, target in pairs):
                raise ValueError(f"{stage} Gate-Gate无向边没有双向存储")
            degree = torch.bincount(
                edge_index[0], minlength=int(graph["gate"].num_nodes)
            )
            if int(degree.max()) > 5:
                raise ValueError(f"{stage} Gate-Gate节点度数超过5")
        label_only_edge_types = set(graph.edge_types) - set(LOGICAL_EDGE_TYPES) - {
            geom_edge_type
        }
        for edge_type in label_only_edge_types:
            store = graph[edge_type]
            if "edge_y" not in store or "edge_y_mask" not in store:
                raise ValueError(f"{stage}/{edge_type}标签边缺少edge_y或mask")
            if "edge_attr" in store and store.edge_attr.shape[1] != 0:
                raise ValueError(f"{stage}/{edge_type}标签值混入模型输入edge_attr")

    reference = graphs[STAGES[0]]
    for stage in STAGES[1:]:
        graph = graphs[stage]
        for edge_type in LOGICAL_EDGE_TYPES:
            if not same_tensor(
                reference[edge_type].edge_index, graph[edge_type].edge_index
            ):
                raise ValueError(f"{stage}逻辑边{edge_type}与floorplan不一致")
        for node_type in LABEL_NODE_TYPES:
            if not same_float_tensor(
                reference[node_type].y, graph[node_type].y
            ):
                raise ValueError(f"{stage}/{node_type}标签与floorplan不一致")
            if not same_tensor(
                reference[node_type].y_valid_mask,
                graph[node_type].y_valid_mask,
            ):
                raise ValueError(f"{stage}/{node_type}标签mask与floorplan不一致")
        shared_edge_types = (
            set(reference.edge_types)
            - set(STAGE_SPECIFIC_EDGE_TYPES)
        )
        if shared_edge_types != (
            set(graph.edge_types) - set(STAGE_SPECIFIC_EDGE_TYPES)
        ):
            raise ValueError(f"{stage}共享边类型集合与floorplan不一致")
        for edge_type in shared_edge_types:
            if not same_tensor(
                reference[edge_type].edge_index,
                graph[edge_type].edge_index,
            ):
                raise ValueError(f"{stage}/{edge_type}共享边索引不一致")
            for attribute in ("edge_y", "edge_y_mask"):
                if attribute in reference[edge_type] and not same_float_tensor(
                    reference[edge_type][attribute],
                    graph[edge_type][attribute],
                ):
                    raise ValueError(
                        f"{stage}/{edge_type}/{attribute}共享边标签不一致"
                    )

    report = {
        "schema": "r2g2_four_stage_validation_v1",
        "status": "PASS",
        "base_graph": {
            "path": str(output / "base_graph/base_graph.pt"),
            "sha256": sha256(output / "base_graph/base_graph.pt"),
        },
        "shared_labels": {
            path.name: sha256(path)
            for path in sorted((output / "labels").glob("*.csv"))
        },
        "stages": stage_stats,
        "direct_base_stage_snapshots": direct_snapshot_stats,
        "checks": {
            "same_eight_csv_headers": True,
            "same_entity_keys_and_order": True,
            "missing_physical_values_are_nan": True,
            "graph_x_preserves_nan": True,
            "route_def_not_used_by_features": True,
            "same_logical_edge_index": True,
            "same_node_labels_and_masks": True,
            "same_shared_edge_labels_and_masks": True,
            "configured_irdrop_labels_are_nonempty": expect_irdrop,
            "configured_timing_labels_are_nonempty": expect_timing,
            "configured_timing_edge_labels_are_nonempty": expect_timing_edges,
            "route_derived_edge_labels_are_label_only": True,
            "auxiliary_relations_are_independent_edge_types": True,
            "auxiliary_payload_not_merged_into_core_edge_attr": True,
            "target_stage_snapshots_present": True,
            "target_stage_snapshots_not_model_input": True,
            "direct_base_to_placement_and_route_snapshots_present": True,
            "fixed_congestion_grid_um": expected_grid_um,
            "fixed_congestion_grid_consistent_across_stages": True,
            "stage_aware_trusted_coordinate_policy": True,
            "stage_aware_hpwl_and_congestion_feature_policy": True,
            "fixed_same_grid_undirected_degree5_gate_gate_edges": True,
        },
    }
    report_path = output / "four_stage.validation.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[check] PASS: {report_path}")


if __name__ == "__main__":
    main()
