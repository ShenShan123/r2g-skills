#!/usr/bin/env python3
"""阶段4：按稳定实体键组合基础拓扑、特征表和多任务原始标签。

最终输出 PyG HeteroData，包含 Gate/Net/IO Pin/Pin 四类节点、三类逻辑正向关系，
一类只供拥塞任务使用的同2.1um网格无向Gate–Gate几何关系；每点最多5个邻居，
floorplan/placement预测因无可信标准单元坐标而不建立该关系。旧实验可显式启用
有向 Pin/IO Pin timing_path 关系，但通用数据集默认只保留节点Slack。RC任务另外使用Net→Net耦合虚拟边和
Driver Pin/IO Pin→Sink Pin/IO Pin等效电阻虚拟边；真实Cc/Reff只存入edge_y。
任何重复键、缺失实体、悬空边或基础图/CSV实体集合不一致都会立即报错；禁止按CSV行号
进行隐式对齐。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import torch
from torch_geometric.data import HeteroData


PREDICTION_STAGES = ("floorplan", "placement", "cts", "route")

FAST_ROUTE_GRID_TRACKS = 15
METAL3_PITCH_UM = 0.14


def resolve_congestion_grid_um(cfg: dict[str, Any]) -> float:
    """与``02_extract_features.resolve_congestion_grid_um``逐字段等价的副本。

    04不导入02(它只读CSV)，所以这里保留一份同语义实现；两处必须同时修改。
    默认值仍是Nangate45的2.1um。
    """

    explicit = cfg.get("congestion_grid_um")
    if explicit not in (None, ""):
        value = float(explicit)
        if value <= 0:
            raise ValueError(f"congestion_grid_um必须为正数: {explicit!r}")
        return value
    tracks = int(cfg.get("congestion_grid_tracks", FAST_ROUTE_GRID_TRACKS))
    pitch = float(cfg.get("congestion_grid_pitch_um", METAL3_PITCH_UM))
    if tracks <= 0 or pitch <= 0:
        raise ValueError(
            "congestion_grid_tracks/congestion_grid_pitch_um必须为正数: "
            f"tracks={tracks!r}, pitch={pitch!r}"
        )
    return tracks * pitch


STAGE_FEATURE_AVAILABILITY = {
    "floorplan": {
        "logical_liberty_sdc_features": True,
        "die_and_io_floorplan_features": False,
        "fixed_macro_coordinates": False,
        "standard_cell_coordinates": False,
        "pin_coordinates": False,
        "hpwl": False,
        "congestion_inputs": False,
        "gate_gate_geometry": False,
    },
    "placement": {
        "logical_liberty_sdc_features": True,
        "die_and_io_floorplan_features": True,
        "fixed_macro_coordinates": True,
        "standard_cell_coordinates": False,
        "pin_coordinates": "fixed_macro_pins_only",
        "hpwl": False,
        "congestion_inputs": False,
        "gate_gate_geometry": False,
    },
    "cts": {
        "logical_liberty_sdc_features": True,
        "die_and_io_floorplan_features": True,
        "fixed_macro_coordinates": True,
        "standard_cell_coordinates": True,
        "pin_coordinates": True,
        "hpwl": True,
        "congestion_inputs": True,
        "gate_gate_geometry": True,
    },
    "route": {
        "logical_liberty_sdc_features": True,
        "die_and_io_floorplan_features": True,
        "fixed_macro_coordinates": True,
        "standard_cell_coordinates": True,
        "pin_coordinates": True,
        "hpwl": True,
        "congestion_inputs": True,
        "gate_gate_geometry": True,
    },
}


CONGESTION_FEATURE_COLUMNS = [
    "congestion_pin_density",
    "congestion_cell_density",
    "congestion_net_density",
    "congestion_rudy",
    "congestion_rudy_pin",
    "congestion_feature_valid",
]
CONGESTION_GEOM_EDGE_TYPE = ("gate", "congestion_geom", "gate")
CONGESTION_GEOM_EDGE_COLUMNS = [
    "euclidean_distance_um",
]
TIMING_EDGE_TYPES = (
    ("pin", "timing_path", "pin"),
    ("pin", "timing_path", "io_pin"),
    ("io_pin", "timing_path", "pin"),
    ("io_pin", "timing_path", "io_pin"),
)
TIMING_EDGE_Y_COLUMNS = ["setup_delay_ns", "hold_delay_ns"]
RC_COUPLING_EDGE_TYPE = ("net", "rc_coupling", "net")
RC_RESISTANCE_EDGE_TYPES = (
    ("pin", "rc_resistance", "pin"),
    ("pin", "rc_resistance", "io_pin"),
    ("io_pin", "rc_resistance", "pin"),
    ("io_pin", "rc_resistance", "io_pin"),
)
RC_COUPLING_EDGE_Y_COLUMNS = ["coupling_cap_pF"]
RC_RESISTANCE_EDGE_Y_COLUMNS = ["effective_resistance_ohm"]


def load_config(path: str) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        return config_path, json.load(handle)


def output_dir_from_config(config_path: Path, cfg: dict[str, Any]) -> Path:
    design = str(cfg.get("design_name") or config_path.stem)
    raw = str(cfg.get("output_dir", f"output/{design}"))
    path = Path(raw)
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def load_encode_maps(
    config_path: Path, cfg: dict[str, Any]
) -> tuple[Path, dict[str, dict[str, int]], list[dict[str, str]]]:
    raw_path = Path(str(cfg.get("encode_map", "encode_map.csv")))
    encode_path = (
        raw_path
        if raw_path.is_absolute()
        else (config_path.parent / raw_path).resolve()
    )
    rows = read_csv(encode_path)
    platform = str(cfg.get("platform", "")).strip().lower()
    maps: dict[str, dict[str, int]] = {}
    selected_rows: list[dict[str, str]] = []
    for row in rows:
        technology = row.get("technology", "").strip().lower()
        if technology not in {"global", "*", platform}:
            continue
        map_name = row["map_name"].strip()
        raw_value = row["raw_value"].strip().upper()
        encoded_id = int(row["encoded_id"])
        mapping = maps.setdefault(map_name, {})
        previous = mapping.get(raw_value)
        if previous is not None and previous != encoded_id:
            raise ValueError(
                f"encode_map存在冲突: {map_name}/{raw_value}={previous},{encoded_id}"
            )
        mapping[raw_value] = encoded_id
        selected_rows.append(dict(row))
    if not maps:
        raise ValueError(f"encode_map没有适用于platform={platform!r}的映射")
    return encode_path, maps, selected_rows


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """同目录临时文件写完后原子替换JSON。"""

    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_torch_save(value: Any, path: Path) -> None:
    """同目录临时文件写完后原子替换PT。"""

    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        torch.save(value, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def index_unique(
    rows: list[dict[str, str]], keys: tuple[str, ...], table_name: str
) -> dict[Any, dict[str, str]]:
    indexed: dict[Any, dict[str, str]] = {}
    duplicates: list[Any] = []
    for row in rows:
        key: Any = tuple(row[name] for name in keys)
        if len(keys) == 1:
            key = key[0]
        if key in indexed:
            duplicates.append(key)
        indexed[key] = row
    if duplicates:
        raise ValueError(f"{table_name} 存在重复实体键，例如 {duplicates[:5]}")
    return indexed


def require_same_keys(
    expected: set[Any],
    actual: set[Any],
    table_name: str,
) -> dict[str, int]:
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        raise ValueError(
            f"{table_name} 与基础图实体不一致: missing={len(missing)} "
            f"extra={len(extra)}, missing示例={list(missing)[:5]}, "
            f"extra示例={list(extra)[:5]}"
        )
    return {"expected": len(expected), "actual": len(actual), "missing": 0, "extra": 0}


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def raw_float(value: Any) -> float:
    """解析原始feature值；缺失和非法值保留为NaN，不在数据集阶段插补。"""

    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def parse_valid_flag(raw: Any, name: str, key: Any) -> bool:
    """严格读取CSV来源有效性；只接受有限的0/1。"""

    try:
        parsed = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{name}不是0/1有效性标志: key={key!r}, value={raw!r}"
        ) from error
    if not math.isfinite(parsed) or parsed not in {0.0, 1.0}:
        raise ValueError(
            f"{name}不是0/1有效性标志: key={key!r}, value={raw!r}"
        )
    return parsed == 1.0


def matrix(
    ordered_keys: list[Any],
    indexed: dict[Any, dict[str, str]],
    columns: list[str],
    graph_id: int = 0,
) -> torch.Tensor:
    if not ordered_keys:
        return torch.empty((0, len(columns) + 1), dtype=torch.float32)
    values = [
        [raw_float(indexed[key].get(column)) for column in columns]
        + [float(graph_id)]
        for key in ordered_keys
    ]
    return torch.tensor(values, dtype=torch.float32)


def vector(
    ordered_keys: list[Any],
    indexed: dict[Any, dict[str, str]],
    column: str,
    valid_column: str = "",
) -> torch.Tensor:
    values: list[float] = []
    for key in ordered_keys:
        row = indexed[key]
        valid = not valid_column or int(finite_float(row.get(valid_column), 0.0)) == 1
        try:
            value = float(row[column])
        except (TypeError, ValueError):
            value = float("nan")
        values.append(value if valid and math.isfinite(value) else float("nan"))
    return torch.tensor(values, dtype=torch.float32)


def validity_vector(
    ordered_keys: list[Any],
    indexed: dict[Any, dict[str, str]],
    valid_column: str,
) -> torch.Tensor:
    """直接从标签CSV的原始valid列构造来源有效性mask。

    该mask只描述EDA标签是否存在并通过对齐，不包含任务选择、数据划分、异常值筛选
    或训练采样策略。
    """

    values: list[bool] = []
    for key in ordered_keys:
        values.append(
            parse_valid_flag(
                indexed[key].get(valid_column, ""),
                valid_column,
                key,
            )
        )
    return torch.tensor(values, dtype=torch.bool)


def require_label_mask_consistency(
    node_type: str,
    y: torch.Tensor,
    y_valid_mask: torch.Tensor,
) -> None:
    """拒绝CSV valid与原始标签NaN约定不一致的节点标签。"""

    if y.shape != y_valid_mask.shape:
        raise ValueError(
            f"{node_type}.y与y_valid_mask形状不一致: "
            f"{tuple(y.shape)} != {tuple(y_valid_mask.shape)}"
        )
    finite_mask = torch.isfinite(y)
    if not torch.equal(finite_mask, y_valid_mask):
        mismatch = torch.nonzero(
            finite_mask != y_valid_mask, as_tuple=False
        )
        raise ValueError(
            f"{node_type}标签CSV valid与y有限性不一致: "
            f"mismatch={int(mismatch.shape[0])}, "
            f"examples={mismatch[:10].tolist()}"
        )


def edge_tensor(
    rows: list[dict[str, str]],
    src_key,
    dst_key,
    src_map: dict[Any, int],
    dst_map: dict[Any, int],
    relation_name: str,
) -> torch.Tensor:
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for row in rows:
        source = src_key(row)
        target = dst_key(row)
        if source not in src_map or target not in dst_map:
            raise ValueError(
                f"{relation_name} 引用不存在实体: src={source!r}, dst={target!r}"
            )
        pair = (src_map[source], dst_map[target])
        if pair in seen:
            raise ValueError(f"{relation_name} 存在重复边: {source!r}->{target!r}")
        seen.add(pair)
        pairs.append(pair)
    return (
        torch.tensor(pairs, dtype=torch.long).t().contiguous()
        if pairs
        else torch.empty((2, 0), dtype=torch.long)
    )


def edge_attributes(
    rows: list[dict[str, str]], columns: list[str]
) -> torch.Tensor:
    if not rows:
        return torch.empty((0, len(columns)), dtype=torch.float32)
    return torch.tensor(
        [[raw_float(row.get(column)) for column in columns] for row in rows],
        dtype=torch.float32,
    )


def edge_labels(
    rows: list[dict[str, str]],
    columns: list[str],
    valid_columns: list[str],
) -> tuple[torch.Tensor, torch.Tensor]:
    """读取原始边标签；无效标签保持NaN并生成显式mask。"""

    if len(columns) != len(valid_columns):
        raise ValueError(
            "边标签列与valid列数量不一致: "
            f"{len(columns)} != {len(valid_columns)}"
        )
    if not rows:
        return (
            torch.empty((0, len(columns)), dtype=torch.float32),
            torch.empty((0, len(columns)), dtype=torch.bool),
        )
    values: list[list[float]] = []
    masks: list[list[bool]] = []
    for row_index, row in enumerate(rows):
        value_row: list[float] = []
        mask_row: list[bool] = []
        for column, valid_column in zip(columns, valid_columns):
            valid = parse_valid_flag(
                row.get(valid_column, ""),
                valid_column,
                row_index,
            )
            try:
                value = float(row.get(column, "nan"))
            except (TypeError, ValueError):
                value = float("nan")
            valid = valid and math.isfinite(value)
            value_row.append(value if valid else float("nan"))
            mask_row.append(valid)
        values.append(value_row)
        masks.append(mask_row)
    return (
        torch.tensor(values, dtype=torch.float32),
        torch.tensor(masks, dtype=torch.bool),
    )


def build_congestion_geom_edges(
    gate_keys: list[str],
    gate_index: dict[str, dict[str, str]],
    cfg: dict[str, Any],
    prediction_stage: str,
    origin_x: float,
    origin_y: float,
) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor], dict[str, Any]]:
    """构造同2.1um网格、无向且每点度数至多5的Gate几何关系。

    PyG用两条反向``edge_index``记录表达一条无向边。候选对按中心距离稳定排序，
    只有两端当前度数均小于容量时才接纳，因此无向度数上限是真正的全局上限。
    floorplan/placement预测没有可信标准单元坐标，几何关系必须为空。
    """

    requested = bool(cfg.get("congestion_geom_edges", True))
    stage_eligible = prediction_stage in {"cts", "route"}
    enabled = requested and stage_eligible
    # r2g-skills delta vs upstream R2G2.0: 上游写死``15.0 * 0.14``(Nangate45
    # Metal3 pitch)。Gate-Gate同格关系必须和02/03的拥塞网格用同一个步长，
    # 否则换平台后"同格"的物理含义与拥塞特征/标签不再一致。
    width = resolve_congestion_grid_um(cfg)
    height = width
    capacity = int(cfg.get("congestion_geom_max_undirected_degree", 5))
    candidate_limit = int(
        cfg.get("congestion_geom_candidate_pair_limit", 10_000_000)
    )
    if capacity < 0:
        raise ValueError("Gate-Gate无向度数上限不能为负数")
    if candidate_limit <= 0:
        raise ValueError("Gate-Gate候选对上限必须为正数")

    available_geometry: dict[int, tuple[float, float]] = {}
    geometry: dict[int, tuple[float, float, float, float]] = {}
    for index, key in enumerate(gate_keys):
        row = gate_index[key]
        if int(finite_float(row.get("placement_valid"))) != 1:
            continue
        x = finite_float(row.get("x_um"))
        y = finite_float(row.get("y_um"))
        center_x = finite_float(row.get("center_x_um"), x)
        center_y = finite_float(row.get("center_y_um"), y)
        available_geometry[index] = (center_x, center_y)
        if not enabled:
            continue
        geometry[index] = (
            x,
            y,
            center_x,
            center_y,
        )

    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for node_id, (x, y, _, _) in geometry.items():
        buckets[
            (
                math.floor((x - origin_x) / width),
                math.floor((y - origin_y) / height),
            )
        ].append(node_id)

    candidate_pair_count = sum(
        len(node_ids) * (len(node_ids) - 1) // 2
        for node_ids in buckets.values()
    )
    if candidate_pair_count > candidate_limit:
        raise ValueError(
            "Gate-Gate候选对超过安全上限；这通常表示把未放置标准单元的临时坐标"
            "当成了可信位置: "
            f"stage={prediction_stage}, candidates={candidate_pair_count}, "
            f"limit={candidate_limit}, max_bucket="
            f"{max((len(v) for v in buckets.values()), default=0)}"
        )

    accepted: list[tuple[int, int, float, int, int]] = []
    degree: dict[int, int] = defaultdict(int)
    for (grid_x, grid_y), node_ids in sorted(buckets.items()):
        ordered = sorted(set(node_ids))
        candidates: list[tuple[float, int, int]] = []
        for position, source in enumerate(ordered):
            sx, sy = geometry[source][2:4]
            for target in ordered[position + 1 :]:
                tx, ty = geometry[target][2:4]
                candidates.append((math.hypot(tx - sx, ty - sy), source, target))
        for distance, source, target in sorted(candidates):
            if degree[source] >= capacity or degree[target] >= capacity:
                continue
            accepted.append((source, target, distance, grid_x, grid_y))
            degree[source] += 1
            degree[target] += 1

    directed = [
        (source, target, distance, grid_x, grid_y)
        for node_u, node_v, distance, grid_x, grid_y in accepted
        for source, target in ((node_u, node_v), (node_v, node_u))
    ]
    directed.sort(key=lambda row: (row[0], row[1], row[3], row[4]))
    edge_index = (
        torch.tensor(
            [[row[0], row[1]] for row in directed], dtype=torch.long
        )
        .t()
        .contiguous()
        if directed
        else torch.empty((2, 0), dtype=torch.long)
    )
    edge_attr = (
        torch.tensor(
            [[row[2]] for row in directed],
            dtype=torch.float32,
        )
        if directed
        else torch.empty(
            (0, len(CONGESTION_GEOM_EDGE_COLUMNS)), dtype=torch.float32
        )
    )
    debug = {
        "window_pass": torch.zeros(len(directed), dtype=torch.int8),
        "grid_x": torch.tensor([row[3] for row in directed], dtype=torch.int32),
        "grid_y": torch.tensor([row[4] for row in directed], dtype=torch.int32),
    }
    connected_nodes = {
        node for row in directed for node in row[:2]
    }
    degree_histogram = {
        str(value): sum(observed == value for observed in degree.values())
        for value in sorted(set(degree.values()))
    }
    stats = {
        "enabled": enabled,
        "requested": requested,
        "prediction_stage": prediction_stage,
        "stage_eligible": stage_eligible,
        "disabled_reason": (
            ""
            if enabled
            else (
                "disabled_by_configuration"
                if not requested
                else "standard_cell_coordinates_not_trusted_at_input_cutoff"
            )
        ),
        "construction": (
            f"fixed_{width:g}um_same_grid_undirected_degree_capped_nearest"
        ),
        "grid_derivation": (
            f"{int(cfg.get('congestion_grid_tracks', FAST_ROUTE_GRID_TRACKS))}"
            "_fast_route_tracks_x_"
            f"{float(cfg.get('congestion_grid_pitch_um', METAL3_PITCH_UM)):g}"
            "um_metal3_pitch"
        ),
        "grid_step_um": width,
        "window_width_um": width,
        "window_height_um": height,
        "link_capacity_per_gate_per_window": capacity,
        "max_undirected_degree": capacity,
        "max_undirected_degree_observed": max(degree.values(), default=0),
        "neighbor_selection": "deterministic_nearest_with_both_endpoint_degree_cap",
        "undirected_storage": "two_symmetric_directed_edge_index_entries",
        "shifted_windows": False,
        "window_passes": 1,
        "window_shifts_um": [[0.0, 0.0]],
        "windows_per_pass": [len(buckets)],
        "cell_bbox_window_membership": False,
        "gate_to_grid_mapping": f"gate_origin_fixed_{width:g}um_grid",
        "multiedges_across_windows_preserved": False,
        "available_trusted_position_gates": len(available_geometry),
        "valid_position_gates": len(geometry),
        "invalid_position_gates": len(gate_keys) - len(available_geometry),
        "connected_gates": len(connected_nodes),
        "isolated_valid_gates": len(geometry) - len(connected_nodes),
        "grid_count": len(buckets),
        "max_bucket_occupancy": max(
            (len(node_ids) for node_ids in buckets.values()), default=0
        ),
        "candidate_undirected_pairs": candidate_pair_count,
        "candidate_pair_limit": candidate_limit,
        "undirected_pairs": len(accepted),
        "unique_directed_pairs": len(directed),
        "directed_edges": len(directed),
        "degree_histogram": degree_histogram,
    }
    return edge_index, edge_attr, debug, stats


def feature_schemas(profile: str) -> dict[str, list[str]]:
    """按预测时点控制输入，默认不把Route阶段结果作为Route标签输入。"""

    gate_logic = [
        "cell_type_id",
        "cell_function_id",
        "is_sequential_cell",
        "is_buffer_cell",
        "is_inverter_cell",
        "is_clock_buffer_cell",
        "is_clock_gate_cell",
        "drive_strength",
        "cell_area_um2",
        "cell_leakage_power",
        "clock_domain_id",
        "timing_forward_level",
        "timing_reverse_level",
        "timing_level_valid",
    ]
    net_logic = [
        "net_type_id",
        "pin_count",
        "fanout",
        "num_drivers",
        "num_sinks",
        "connects_macro_flag",
        "is_clock_net",
        "clock_domain_id",
        "total_sink_cap_fF",
    ]
    io_logic = [
        "pin_direction_id",
        "pin_role_id",
        "is_clock_port",
        "is_driver_pin",
        "is_sink_pin",
        "is_timing_startpoint",
        "is_timing_endpoint",
        "clock_domain_id",
        "clock_period_ns",
        "clock_uncertainty_ns",
        "clock_constraint_valid",
        "input_delay_ns",
        "output_delay_ns",
        "io_constraint_valid",
        "pin_layer_id",
        "timing_forward_level",
        "timing_reverse_level",
        "timing_level_valid",
        "net_type_id",
    ]
    pin_logic = [
        "pin_type_id",
        "pin_role_id",
        "pin_direction_id",
        "pin_cap_fF",
        "pin_max_transition_ns",
        "pin_max_capacitance_fF",
        "cell_type_id",
        "cell_function_id",
        "owner_drive_strength",
        "is_clock_pin",
        "is_data_pin",
        "is_reset_pin",
        "is_set_pin",
        "is_enable_pin",
        "is_sequential_pin",
        "is_combinational_pin",
        "is_driver_pin",
        "is_sink_pin",
        "is_timing_startpoint",
        "is_timing_endpoint",
        "clock_domain_id",
        "clock_period_ns",
        "clock_uncertainty_ns",
        "clock_constraint_valid",
        "timing_forward_level",
        "timing_reverse_level",
        "timing_level_valid",
    ]
    if profile == "strict_pre_floorplan":
        return {
            "gate": gate_logic,
            "net": net_logic,
            "io_pin": io_logic,
            "pin": pin_logic,
        }
    net_columns = [
        *net_logic,
        "net_bbox_width_um",
        "net_bbox_height_um",
        "hpwl_um",
        "stage_net_count",
        "stage_direct_net_count",
        "stage_inferred_backend_net_count",
        "stage_net_split_flag",
        "stage_net_renamed_flag",
        "stage_net_anchor_count",
        "stage_net_anchor_coverage",
        "stage_net_alignment_valid",
        "stage_lineage_ambiguous_flag",
        "stage_lineage_valid",
        "stage_segment_total_hpwl_um",
        "stage_segment_max_hpwl_um",
        "stage_segment_mean_hpwl_um",
    ]
    if profile == "all":
        net_columns.append("route_layer_count")
    return {
        "gate": [
            "x_um",
            "y_um",
            "center_x_um",
            "center_y_um",
            "center_x_normalized",
            "center_y_normalized",
            *gate_logic,
            "orientation_id",
            "placement_status_id",
            "placement_valid",
            *CONGESTION_FEATURE_COLUMNS,
        ],
        "net": [
            *net_columns,
            "hpwl_valid",
            "stage_segment_hpwl_valid",
        ],
        "io_pin": [
            "pin_x_um",
            "pin_y_um",
            "pin_x_normalized",
            "pin_y_normalized",
            "distance_to_die_left_um",
            "distance_to_die_right_um",
            "distance_to_die_bottom_um",
            "distance_to_die_top_um",
            "pin_position_valid",
            *io_logic,
            "nearest_tap_distance_um",
        ],
        "pin": [
            *pin_logic,
            "pin_x_um",
            "pin_y_um",
            "pin_x_normalized",
            "pin_y_normalized",
            "distance_to_die_left_um",
            "distance_to_die_right_um",
            "distance_to_die_bottom_um",
            "distance_to_die_top_um",
            "pin_position_valid",
        ],
    }


def global_schema(profile: str) -> list[str]:
    base = [
        "num_logical_cells",
        "num_logical_nets",
        "num_ios",
        "avg_fanout",
        "die_width_um",
        "die_height_um",
        "die_area_um2",
        "dbu_per_um",
        "place_density",
        "place_density_is_default",
        "core_utilization",
        "abc_area",
        "total_lib_pin_cap_fF",
        "v_nom",
        "freq_hz",
        "num_clocks",
        "min_clock_period_ns",
        "max_clock_period_ns",
        "avg_clock_period_ns",
    ]
    if profile == "all":
        base.extend(["avg_route_layers_per_net", "avg_tracks_per_layer"])
    if profile == "strict_pre_floorplan":
        return [
            "num_logical_cells",
            "num_logical_nets",
            "num_ios",
            "avg_fanout",
            "total_lib_pin_cap_fF",
            "v_nom",
            "freq_hz",
            "num_clocks",
            "min_clock_period_ns",
            "max_clock_period_ns",
            "avg_clock_period_ns",
        ]
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description="组合四阶段四节点异构图")
    parser.add_argument("--config", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--features", default="")
    parser.add_argument("--labels", default="")
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--stage",
        choices=PREDICTION_STAGES,
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--feature-profile",
        choices=("pre_route", "strict_pre_floorplan", "all"),
        default="",
        help="默认pre_route；all包含route_layer_count，存在标签阶段泄漏风险",
    )
    args = parser.parse_args()

    config_path, cfg = load_config(args.config)
    design = str(cfg.get("design_name") or config_path.stem)
    root_out = output_dir_from_config(config_path, cfg)
    if not args.stage:
        for prediction_stage in PREDICTION_STAGES:
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(config_path),
                "--stage",
                prediction_stage,
            ]
            if args.base:
                command.extend(["--base", str(Path(args.base).resolve())])
            if args.labels:
                command.extend(["--labels", str(Path(args.labels).resolve())])
            if args.feature_profile:
                command.extend(["--feature-profile", args.feature_profile])
            subprocess.run(command, check=True)
        print(f"[assemble] four-stage bundle completed: {root_out / 'stages'}")
        return
    prediction_stage = args.stage
    encode_map_path, encoding_maps, encoding_rows = load_encode_maps(
        config_path, cfg
    )
    base_path = (
        Path(args.base).resolve()
        if args.base
        else root_out / "base_graph" / "base_graph.pt"
    )
    feature_dir = (
        Path(args.features).resolve()
        if args.features
        else root_out / "stages" / prediction_stage / "features"
    )
    label_dir = (
        Path(args.labels).resolve()
        if args.labels
        else root_out / "labels"
    )
    out_path = (
        Path(args.out).resolve()
        if args.out
        else root_out / "stages" / prediction_stage / "heterograph.pt"
    )
    profile = args.feature_profile or str(cfg.get("feature_profile", "pre_route"))
    if profile not in {"pre_route", "strict_pre_floorplan", "all"}:
        raise ValueError(f"未知feature_profile: {profile}")

    base = torch.load(base_path, map_location="cpu", weights_only=False)
    if dict(base.cell_type_map) != encoding_maps.get("cell_type_id", {}):
        raise ValueError(
            "base_graph.pt的cell_type_map与当前encode_map.csv不一致"
        )
    tables = {
        "metadata": read_csv(feature_dir / "metadata.csv"),
        "gate": read_csv(feature_dir / "nodes_gate.csv"),
        "net": read_csv(feature_dir / "nodes_net.csv"),
        "io_pin": read_csv(feature_dir / "nodes_iopin.csv"),
        "pin": read_csv(feature_dir / "nodes_pin.csv"),
        "gate_pin": read_csv(feature_dir / "edges_gate_pin.csv"),
        "pin_net": read_csv(feature_dir / "edges_pin_net.csv"),
        "io_net": read_csv(feature_dir / "edges_iopin_net.csv"),
        "gate_labels": read_csv(label_dir / "gate_con_IR.csv"),
        "net_labels": read_csv(label_dir / "net_wirelength_Cg.csv"),
    }
    if len(tables["metadata"]) != 1:
        raise ValueError("metadata.csv必须且只能包含一行")
    metadata_prediction_stage = tables["metadata"][0].get(
        "prediction_stage", ""
    )
    if metadata_prediction_stage != prediction_stage:
        raise ValueError(
            "特征阶段与组装目标不一致: "
            f"metadata={metadata_prediction_stage!r}, "
            f"assemble={prediction_stage!r}"
        )
    expected_geom_eligible = int(prediction_stage in {"cts", "route"})
    metadata_geom_eligible = int(
        finite_float(tables["metadata"][0].get("gate_geom_edge_eligible"))
    )
    if metadata_geom_eligible != expected_geom_eligible:
        raise ValueError(
            "特征坐标可信性与Gate-Gate阶段策略不一致: "
            f"stage={prediction_stage}, metadata={metadata_geom_eligible}, "
            f"expected={expected_geom_eligible}"
        )
    pin_timing_path = label_dir / "pin_timing.csv"
    iopin_timing_path = label_dir / "iopin_timing.csv"
    timing_labels_enabled = bool(
        cfg.get(
            "timing_enabled",
            pin_timing_path.is_file() or iopin_timing_path.is_file(),
        )
    )
    timing_file_flags = {
        "pin_timing.csv": pin_timing_path.is_file(),
        "iopin_timing.csv": iopin_timing_path.is_file(),
    }
    if timing_labels_enabled and not all(timing_file_flags.values()):
        raise ValueError(
            "Timing节点标签必须成组出现，缺失文件: "
            f"{[name for name, present in timing_file_flags.items() if not present]}"
        )
    timing_present = timing_labels_enabled and all(
        timing_file_flags.values()
    )
    tables["pin_timing"] = (
        read_csv(pin_timing_path) if timing_present else []
    )
    tables["iopin_timing"] = (
        read_csv(iopin_timing_path) if timing_present else []
    )
    timing_edge_path = label_dir / "edges_timing_path.csv"
    timing_edges_enabled = bool(
        cfg.get("timing_use_report_path_edges", False)
    )
    timing_edges_present = (
        timing_present and timing_edges_enabled and timing_edge_path.is_file()
    )
    timing_edges = read_csv(timing_edge_path) if timing_edges_present else []
    for source_type, _, target_type in TIMING_EDGE_TYPES:
        key = f"{source_type}|timing_path|{target_type}"
        tables[key] = [
            row
            for row in timing_edges
            if row["src_node_type"] == source_type
            and row["dst_node_type"] == target_type
        ]
    rc_paths = {
        "net_coupling": label_dir / "edges_net_net_Cc.csv",
        "pin_net_resistance": label_dir / "edges_pin_pin_Reff.csv",
    }
    rc_flags = {name: path.is_file() for name, path in rc_paths.items()}
    if any(rc_flags.values()) and not all(rc_flags.values()):
        raise ValueError(
            "RC标签必须成组出现，缺失文件: "
            f"{[name for name, present in rc_flags.items() if not present]}"
        )
    rc_present = all(rc_flags.values())
    for name, path in rc_paths.items():
        tables[name] = read_csv(path) if rc_present else []

    gate_index = index_unique(tables["gate"], ("inst_name",), "nodes_gate.csv")
    net_index = index_unique(tables["net"], ("net_name",), "nodes_net.csv")
    io_index = index_unique(tables["io_pin"], ("iopin_name",), "nodes_iopin.csv")
    pin_index = index_unique(
        tables["pin"], ("inst_name", "pin_name"), "nodes_pin.csv"
    )
    gate_label_index = index_unique(
        tables["gate_labels"], ("inst_name",), "gate_con_IR.csv"
    )
    net_label_index = index_unique(
        tables["net_labels"], ("net_name",), "net_wirelength_Cg.csv"
    )
    pin_timing_index = (
        index_unique(
            tables["pin_timing"],
            ("inst_name", "pin_name"),
            "pin_timing.csv",
        )
        if timing_present
        else {}
    )
    iopin_timing_index = (
        index_unique(
            tables["iopin_timing"], ("iopin_name",), "iopin_timing.csv"
        )
        if timing_present
        else {}
    )
    metadata_index = index_unique(tables["metadata"], ("graph_id",), "metadata.csv")

    gate_keys = list(base.gate_names)
    net_keys = list(base.net_names)
    pin_keys = list(
        dict.fromkeys(
            zip(base.connection_inst_names, base.connection_pin_names)
        )
    )
    io_keys = sorted(io_index)
    expected_incidence = set(
        zip(
            base.connection_inst_names,
            base.connection_pin_names,
            base.connection_net_names,
        )
    )
    actual_pin_net_incidence = {
        (row["inst_name"], row["pin_name"], row["net_name"])
        for row in tables["pin_net"]
    }
    actual_gate_pin_incidence = {
        (row["inst_name"], row["pin_name"]) for row in tables["gate_pin"]
    }
    expected_io_incidence = set(zip(base.io_pin_names, base.io_pin_net_names))
    actual_io_incidence = {
        (row["iopin_name"], row["net_name"]) for row in tables["io_net"]
    }
    alignment = {
        "gate_features": require_same_keys(
            set(gate_keys), set(gate_index), "nodes_gate.csv"
        ),
        "net_features": require_same_keys(
            set(net_keys), set(net_index), "nodes_net.csv"
        ),
        "pin_features": require_same_keys(
            set(pin_keys), set(pin_index), "nodes_pin.csv"
        ),
        # r2g-skills delta vs upstream R2G2.0: 上游对gate/net/pin三张节点表都
        # 做了require_same_keys, 唯独漏掉nodes_iopin.csv, 而io_keys直接取自
        # 该CSV(sorted(io_index))。因此IO Pin节点集是"CSV说了算"而不是"base
        # 说了算" —— 少一个IO端口只会安静地少一个节点。这里补齐对称检查。
        "io_features": require_same_keys(
            set(str(name) for name in base.io_pin_names),
            set(io_index),
            "nodes_iopin.csv",
        ),
        "gate_labels": require_same_keys(
            set(gate_keys), set(gate_label_index), "gate_con_IR.csv"
        ),
        "net_labels": require_same_keys(
            set(net_keys), set(net_label_index), "net_wirelength_Cg.csv"
        ),
        "gate_pin_edges": require_same_keys(
            set(pin_keys), actual_gate_pin_incidence, "edges_gate_pin.csv"
        ),
        "pin_net_edges": require_same_keys(
            expected_incidence,
            actual_pin_net_incidence,
            "edges_pin_net.csv",
        ),
        "io_net_edges": require_same_keys(
            expected_io_incidence,
            actual_io_incidence,
            "edges_iopin_net.csv",
        ),
    }
    alignment["gate_irdrop_labels"] = require_same_keys(
        set(gate_keys), set(gate_label_index), "gate_con_IR.csv"
    )
    if timing_present:
        alignment["pin_timing_labels"] = require_same_keys(
            set(pin_keys), set(pin_timing_index), "pin_timing.csv"
        )
        alignment["iopin_timing_labels"] = require_same_keys(
            set(io_keys), set(iopin_timing_index), "iopin_timing.csv"
        )
    else:
        alignment["pin_timing_labels"] = {
            "expected": len(pin_keys),
            "actual": 0,
            "missing": len(pin_keys),
            "extra": 0,
            "optional_source_absent": True,
        }
        alignment["iopin_timing_labels"] = {
            "expected": len(io_keys),
            "actual": 0,
            "missing": len(io_keys),
            "extra": 0,
            "optional_source_absent": True,
        }
    if rc_present:
        alignment["net_ground_cap_labels"] = require_same_keys(
            set(net_keys), set(net_label_index), "net_wirelength_Cg.csv"
        )
        coupling_endpoints = {
            row["net1_name"] for row in tables["net_coupling"]
        } | {
            row["net2_name"] for row in tables["net_coupling"]
        }
        missing_coupling_endpoints = coupling_endpoints - set(net_keys)
        if missing_coupling_endpoints:
            raise ValueError(
                "edges_net_net_Cc.csv引用基础图外Net: "
                f"{list(missing_coupling_endpoints)[:5]}"
            )
        alignment["net_coupling_edges"] = {
            "source_unordered_pairs": len(tables["net_coupling"]),
            "aligned_unordered_pairs": len(tables["net_coupling"]),
            "missing_endpoints": 0,
        }
        invalid_rc_node_types = {
            row.get(column, "")
            for row in tables["pin_net_resistance"]
            for column in ("src_node_type", "dst_node_type")
            if row.get(column, "") not in {"pin", "io_pin"}
        }
        if invalid_rc_node_types:
            raise ValueError(
                "edges_pin_pin_Reff.csv存在非法节点类型: "
                f"{sorted(invalid_rc_node_types)}"
            )
        alignment["pin_net_resistance_edges"] = {
            "aligned_directed_edges": len(tables["pin_net_resistance"]),
            "missing_endpoints": 0,
        }
    else:
        alignment["net_ground_cap_labels"] = require_same_keys(
            set(net_keys), set(net_label_index), "net_wirelength_Cg.csv"
        )
    if design not in metadata_index:
        raise ValueError(f"metadata.csv 缺少 graph_id={design}")
    metadata = metadata_index[design]
    label_grid_steps = {
        (
            finite_float(row.get("grid_step_x_um")),
            finite_float(row.get("grid_step_y_um")),
        )
        for row in tables["gate_labels"]
    }
    feature_grid_step = (
        finite_float(metadata.get("congestion_grid_step_x_um")),
        finite_float(metadata.get("congestion_grid_step_y_um")),
    )
    congestion_feature_available = any(
        finite_float(row.get("congestion_feature_valid")) > 0
        for row in tables["gate"]
    )
    # r2g-skills delta vs upstream R2G2.0 (D7): upstream compared the two grid
    # specs with exact float set equality. The feature side records
    # ``tracks * pitch`` directly; the label side round-trips through integer
    # DBU (``round(grid*dbu)/dbu``). Those agree bit-for-bit only when
    # ``tracks*pitch`` happens to land on a representable DBU multiple --
    # true for Nangate45 (15x0.14 -> 2.1) and sky130hd (15x0.46 -> 6.9), FALSE
    # for sky130hs (15x0.48 -> 7.199999999999999 vs 7.2). Upstream never saw it
    # because its one verified platform is in the lucky set. Compare on the
    # physical value with a tolerance; a genuinely different grid (2.1 vs 6.9)
    # is still rejected, and the DBU step is re-checked exactly by
    # checks/validate_four_stage.py.
    grid_mismatch = not all(
        math.isclose(label_step[axis], feature_grid_step[axis], rel_tol=1e-9, abs_tol=1e-12)
        for label_step in label_grid_steps
        for axis in (0, 1)
    ) or len(label_grid_steps) != 1
    if congestion_feature_available and (
        grid_mismatch
        or min(feature_grid_step) <= 0
    ):
        raise ValueError(
            "当前阶段拥塞特征与标签的GCell规格不一致: "
            f"feature={feature_grid_step}, label={sorted(label_grid_steps)}"
        )
    alignment["congestion_grid_spec"] = {
        "expected": 1,
        "actual": int(congestion_feature_available),
        "missing": int(not congestion_feature_available),
        "extra": 0,
        "feature_available_at_cutoff": congestion_feature_available,
    }

    schemas = feature_schemas(profile)
    data = HeteroData()
    data["gate"].x = matrix(gate_keys, gate_index, schemas["gate"])
    data["net"].x = matrix(net_keys, net_index, schemas["net"])
    data["io_pin"].x = matrix(io_keys, io_index, schemas["io_pin"])
    data["pin"].x = matrix(pin_keys, pin_index, schemas["pin"])
    gate_congestion = vector(
        gate_keys,
        gate_label_index,
        "cell_congestion",
        "congestion_valid",
    )
    gate_irdrop = vector(
        gate_keys,
        gate_label_index,
        "ir_drop_mV",
        "irdrop_valid",
    )
    data["gate"].y = torch.stack([gate_congestion, gate_irdrop], dim=1)
    data["gate"].y_valid_mask = torch.stack(
        [
            validity_vector(
                gate_keys, gate_label_index, "congestion_valid"
            ),
            validity_vector(gate_keys, gate_label_index, "irdrop_valid"),
        ],
        dim=1,
    )
    net_wirelength = vector(
        net_keys,
        net_label_index,
        "wirelength_um",
        "wirelength_valid",
    )
    net_ground_cap = vector(
        net_keys,
        net_label_index,
        "ground_cap_pF",
        "ground_cap_valid",
    )
    data["net"].y = torch.stack([net_wirelength, net_ground_cap], dim=1)
    data["net"].y_valid_mask = torch.stack(
        [
            validity_vector(
                net_keys, net_label_index, "wirelength_valid"
            ),
            validity_vector(
                net_keys, net_label_index, "ground_cap_valid"
            ),
        ],
        dim=1,
    )
    if timing_present:
        data["pin"].y = torch.stack(
            [
                vector(
                    pin_keys,
                    pin_timing_index,
                    "setup_slack_ns",
                    "setup_valid",
                ),
                vector(
                    pin_keys,
                    pin_timing_index,
                    "hold_slack_ns",
                    "hold_valid",
                ),
            ],
            dim=1,
        )
        data["pin"].y_valid_mask = torch.stack(
            [
                validity_vector(
                    pin_keys, pin_timing_index, "setup_valid"
                ),
                validity_vector(
                    pin_keys, pin_timing_index, "hold_valid"
                ),
            ],
            dim=1,
        )
        data["io_pin"].y = torch.stack(
            [
                vector(
                    io_keys,
                    iopin_timing_index,
                    "setup_slack_ns",
                    "setup_valid",
                ),
                vector(
                    io_keys,
                    iopin_timing_index,
                    "hold_slack_ns",
                    "hold_valid",
                ),
            ],
            dim=1,
        )
        data["io_pin"].y_valid_mask = torch.stack(
            [
                validity_vector(
                    io_keys, iopin_timing_index, "setup_valid"
                ),
                validity_vector(
                    io_keys, iopin_timing_index, "hold_valid"
                ),
            ],
            dim=1,
        )
    else:
        data["pin"].y = torch.full(
            (len(pin_keys), 2), float("nan"), dtype=torch.float32
        )
        data["io_pin"].y = torch.full(
            (len(io_keys), 2), float("nan"), dtype=torch.float32
        )
        data["pin"].y_valid_mask = torch.zeros(
            (len(pin_keys), 2), dtype=torch.bool
        )
        data["io_pin"].y_valid_mask = torch.zeros(
            (len(io_keys), 2), dtype=torch.bool
        )
    for node_type in ("gate", "net", "pin", "io_pin"):
        require_label_mask_consistency(
            node_type,
            data[node_type].y,
            data[node_type].y_valid_mask,
        )
    gate_map = {key: index for index, key in enumerate(gate_keys)}
    net_map = {key: index for index, key in enumerate(net_keys)}
    io_map = {key: index for index, key in enumerate(io_keys)}
    pin_map = {key: index for index, key in enumerate(pin_keys)}

    gate_pin_rows = tables["gate_pin"]
    pin_net_rows = tables["pin_net"]
    io_net_rows = tables["io_net"]
    data["gate", "has", "pin"].edge_index = edge_tensor(
        gate_pin_rows,
        lambda row: row["inst_name"],
        lambda row: (row["inst_name"], row["pin_name"]),
        gate_map,
        pin_map,
        "gate-has-pin",
    )
    data["gate", "has", "pin"].edge_attr = edge_attributes(
        gate_pin_rows, ["cell_type_id", "pin_type_id"]
    )
    data["pin", "connects_to", "net"].edge_index = edge_tensor(
        pin_net_rows,
        lambda row: (row["inst_name"], row["pin_name"]),
        lambda row: row["net_name"],
        pin_map,
        net_map,
        "pin-connects_to-net",
    )
    data["pin", "connects_to", "net"].edge_attr = edge_attributes(
        pin_net_rows, ["pin_type_id", "net_type_id"]
    )
    data["io_pin", "connects_to", "net"].edge_index = edge_tensor(
        io_net_rows,
        lambda row: row["iopin_name"],
        lambda row: row["net_name"],
        io_map,
        net_map,
        "io_pin-connects_to-net",
    )
    data["io_pin", "connects_to", "net"].edge_attr = edge_attributes(
        io_net_rows, ["pin_direction_id", "net_type_id"]
    )
    if timing_edges_present:
        node_maps = {"pin": pin_map, "io_pin": io_map}
        for source_type, _, target_type in TIMING_EDGE_TYPES:
            edge_type = (source_type, "timing_path", target_type)
            rows = tables["|".join(edge_type)]
            # 以当前图视图和实际监督样本为准：没有正样本的关系不创建空edge
            # store，避免训练端误以为该任务关系在此设计中可用。
            if not rows:
                continue
            if source_type == "pin":
                source_key = lambda row: (
                    row["src_inst_name"],
                    row["src_pin_name"],
                )
            else:
                source_key = lambda row: row["src_iopin_name"]
            if target_type == "pin":
                target_key = lambda row: (
                    row["dst_inst_name"],
                    row["dst_pin_name"],
                )
            else:
                target_key = lambda row: row["dst_iopin_name"]
            data[edge_type].edge_index = edge_tensor(
                rows,
                source_key,
                target_key,
                node_maps[source_type],
                node_maps[target_type],
                "|".join(edge_type),
            )
            # Delay是监督标签而非输入特征，避免把OpenSTA真值泄漏给模型。
            data[edge_type].edge_attr = torch.empty(
                (len(rows), 0), dtype=torch.float32
            )
            edge_y, edge_y_mask = edge_labels(
                rows,
                TIMING_EDGE_Y_COLUMNS,
                ["setup_valid", "hold_valid"],
            )
            data[edge_type].edge_y = edge_y
            data[edge_type].edge_y_mask = edge_y_mask
    if rc_present:
        # Cc物理关系无方向；PyG消息传播使用两个方向，两个方向共享同一原始标签。
        coupling_directed: list[dict[str, str]] = []
        for row in tables["net_coupling"]:
            forward = dict(row)
            forward["src_net_name"] = row["net1_name"]
            forward["dst_net_name"] = row["net2_name"]
            reverse = dict(row)
            reverse["src_net_name"] = row["net2_name"]
            reverse["dst_net_name"] = row["net1_name"]
            coupling_directed.extend([forward, reverse])
        if coupling_directed:
            data[RC_COUPLING_EDGE_TYPE].edge_index = edge_tensor(
                coupling_directed,
                lambda row: row["src_net_name"],
                lambda row: row["dst_net_name"],
                net_map,
                net_map,
                "|".join(RC_COUPLING_EDGE_TYPE),
            )
            data[RC_COUPLING_EDGE_TYPE].edge_attr = torch.empty(
                (len(coupling_directed), 0), dtype=torch.float32
            )
            coupling_y, coupling_y_mask = edge_labels(
                coupling_directed,
                RC_COUPLING_EDGE_Y_COLUMNS,
                ["valid"],
            )
            data[RC_COUPLING_EDGE_TYPE].edge_y = coupling_y
            data[RC_COUPLING_EDGE_TYPE].edge_y_mask = coupling_y_mask

        rc_node_maps = {"pin": pin_map, "io_pin": io_map}
        for source_type, _, target_type in RC_RESISTANCE_EDGE_TYPES:
            edge_type = (source_type, "rc_resistance", target_type)
            rows = [
                row
                for row in tables["pin_net_resistance"]
                if row["src_node_type"] == source_type
                and row["dst_node_type"] == target_type
            ]
            if not rows:
                continue
            source_key = (
                (lambda row: (row["src_inst_name"], row["src_pin_name"]))
                if source_type == "pin"
                else (lambda row: row["src_iopin_name"])
            )
            target_key = (
                (lambda row: (row["dst_inst_name"], row["dst_pin_name"]))
                if target_type == "pin"
                else (lambda row: row["dst_iopin_name"])
            )
            data[edge_type].edge_index = edge_tensor(
                rows,
                source_key,
                target_key,
                rc_node_maps[source_type],
                rc_node_maps[target_type],
                "|".join(edge_type),
            )
            data[edge_type].edge_attr = torch.empty(
                (len(rows), 0), dtype=torch.float32
            )
            resistance_y, resistance_y_mask = edge_labels(
                rows,
                RC_RESISTANCE_EDGE_Y_COLUMNS,
                ["valid"],
            )
            data[edge_type].edge_y = resistance_y
            data[edge_type].edge_y_mask = resistance_y_mask
    geom_cfg = dict(cfg)
    if profile == "strict_pre_floorplan":
        # 该profile模拟无Placement几何的更早阶段，论文定义此时EG必须为空。
        geom_cfg["congestion_geom_edges"] = False
    geom_edge_index, geom_edge_attr, _, geom_stats = (
        build_congestion_geom_edges(
            gate_keys,
            gate_index,
            geom_cfg,
            prediction_stage,
            finite_float(metadata.get("die_left_um")),
            finite_float(metadata.get("die_bottom_um")),
        )
    )
    # 早期阶段不是“有这种边但边数为0”，而是该物理关系尚不可定义。
    # 只有post-placement/post-CTS输入具备可信标准单元坐标时才创建edge store，
    # 让模型可以直接根据图视图判断该辅助关系是否存在。
    if bool(geom_stats["enabled"]):
        data[CONGESTION_GEOM_EDGE_TYPE].edge_index = geom_edge_index
        data[CONGESTION_GEOM_EDGE_TYPE].edge_attr = geom_edge_attr

    # 每个内部Pin必须恰好有一条Gate归属边和一条Net连接边。
    if data["gate", "has", "pin"].num_edges != len(pin_keys):
        raise ValueError("Gate→Pin边数不等于Pin节点数")
    if data["pin", "connects_to", "net"].num_edges != len(pin_keys):
        raise ValueError("Pin→Net边数不等于Pin节点数")
    if data["io_pin", "connects_to", "net"].num_edges != len(io_keys):
        raise ValueError("IO Pin→Net边数不等于IO Pin节点数")

    data["gate"].inst_name = gate_keys
    data["net"].net_name = net_keys
    data["io_pin"].iopin_name = io_keys
    data["pin"].inst_name = [key[0] for key in pin_keys]
    data["pin"].pin_name = [key[1] for key in pin_keys]

    global_columns = global_schema(profile)
    data.global_features = torch.tensor(
        [[raw_float(metadata.get(column)) for column in global_columns]],
        dtype=torch.float32,
    )
    die_width = raw_float(metadata.get("die_width_um"))
    die_height = raw_float(metadata.get("die_height_um"))
    die_left = raw_float(metadata.get("die_left_um"))
    die_bottom = raw_float(metadata.get("die_bottom_um"))
    data.die_coordinates = torch.tensor(
        [
            [
                [die_left, die_bottom],
                [die_left + die_width, die_bottom + die_height],
            ]
        ],
        dtype=torch.float32,
    )
    data.num_subgraphs = 1
    data.design_name = design
    data.prediction_stage = prediction_stage
    data.feature_cutoff = str(metadata.get("feature_cutoff", ""))
    data.task_stage = prediction_stage
    data.input_stage = data.feature_cutoff
    data.topology_source = getattr(base, "topology_source", "")
    data.data_contract_version = "r2g2_four_stage_hetero_pipeline_v2"
    data.feature_availability_contract = STAGE_FEATURE_AVAILABILITY[
        prediction_stage
    ]
    data.auxiliary_edge_availability_contract = {
        "logical_gate_pin_net_edges": "model_input_all_stages",
        "gate_congestion_geom_edges": (
            "model_input_cts_and_route_only"
        ),
        "timing_path_edges": "label_only_all_stages_when_available",
        "rc_coupling_edges": "label_only_all_stages_when_available",
        "rc_resistance_edges": "label_only_all_stages_when_available",
        "label_only_edge_encoder_policy": "exclude_from_message_passing_input",
    }
    auxiliary_edge_types = [
        edge_type
        for edge_type in data.edge_types
        if edge_type[1]
        in {"congestion_geom", "timing_path", "rc_coupling", "rc_resistance"}
    ]
    supervision_edge_types = [
        edge_type
        for edge_type in auxiliary_edge_types
        if edge_type[1] in {"timing_path", "rc_coupling", "rc_resistance"}
    ]
    data.default_message_passing_edge_types = [
        "|".join(edge_type)
        for edge_type in (
            ("gate", "has", "pin"),
            ("pin", "connects_to", "net"),
            ("io_pin", "connects_to", "net"),
        )
    ]
    data.auxiliary_edge_types = [
        "|".join(edge_type) for edge_type in auxiliary_edge_types
    ]
    data.optional_message_passing_edge_types = [
        "|".join(edge_type)
        for edge_type in auxiliary_edge_types
        if edge_type[1] == "congestion_geom"
    ]
    data.supervision_edge_types = [
        "|".join(edge_type) for edge_type in supervision_edge_types
    ]
    data.auxiliary_edge_storage_contract = (
        "each auxiliary relation is an independent HeteroData edge type; "
        "never concatenate geometry/timing/RC payload into core view edge_attr; "
        "timing/RC labels live only in edge_y with edge_y_mask"
    )
    data.shared_label_contract = {
        "source_stage": "route_or_post_route",
        "same_values_and_masks_across_all_prediction_stages": True,
        "labels_are_not_model_input_features": True,
    }
    data.global_feature_schema = global_columns
    data.y_is_raw_physical_value = True
    data.node_y_valid_mask_semantics = (
        "source_validity_from_label_csv_not_training_selection"
    )
    data.normalization_applied = False
    data.filtering_applied = False
    data.data_split_applied = False
    data.encode_map_file = str(encode_map_path)
    data.encode_map_sha256 = sha256_file(encode_map_path)
    data.leakage_warning = (
        "profile=all包含route_layer_count，不能作为route "
        "wirelength/congestion/irdrop正式输入"
        if profile == "all"
        else ""
    )

    # Schema下沉到对应Node/Edge store，避免顶层嵌套dict。
    for node_type, columns in schemas.items():
        data[node_type].x_schema = [*columns, "graph_id"]
        data[node_type].y_valid_mask_semantics = (
            "source_validity_from_label_csv_not_training_selection"
        )
    data["gate"].y_schema = ["cell_congestion", "ir_drop_mV"]
    data["gate"].y_valid_mask_schema = list(data["gate"].y_schema)
    data["gate"].y_unit = ["dimensionless", "mV"]
    data["gate"].label_transform = "none"
    data["net"].y_schema = ["routed_wirelength_um", "ground_cap_pF"]
    data["net"].y_valid_mask_schema = list(data["net"].y_schema)
    data["net"].y_unit = ["um", "pF"]
    data["net"].label_transform = "none"
    data["io_pin"].y_schema = ["setup_slack_ns", "hold_slack_ns"]
    data["io_pin"].y_valid_mask_schema = list(data["io_pin"].y_schema)
    data["io_pin"].y_unit = ["ns", "ns"]
    data["io_pin"].label_transform = "none"
    data["pin"].y_schema = ["setup_slack_ns", "hold_slack_ns"]
    data["pin"].y_valid_mask_schema = list(data["pin"].y_schema)
    data["pin"].y_unit = ["ns", "ns"]
    data["pin"].label_transform = "none"
    edge_schemas = {
        "gate→pin": ["cell_type_id", "pin_type_id"],
        "pin→net": ["pin_type_id", "net_type_id"],
        "io_pin→net": ["pin_direction_id", "net_type_id"],
        "gate→congestion_geom→gate": CONGESTION_GEOM_EDGE_COLUMNS,
    }
    data["gate", "has", "pin"].edge_schema = edge_schemas["gate→pin"]
    data["pin", "connects_to", "net"].edge_schema = edge_schemas["pin→net"]
    data["io_pin", "connects_to", "net"].edge_schema = edge_schemas["io_pin→net"]
    if CONGESTION_GEOM_EDGE_TYPE in data.edge_types:
        data[CONGESTION_GEOM_EDGE_TYPE].edge_schema = edge_schemas[
            "gate→congestion_geom→gate"
        ]
    if timing_edges_present:
        for edge_type in TIMING_EDGE_TYPES:
            if edge_type not in data.edge_types:
                continue
            data[edge_type].edge_schema = []
            data[edge_type].edge_y_schema = TIMING_EDGE_Y_COLUMNS
            data[edge_type].edge_y_unit = ["ns", "ns"]
            data[edge_type].label_transform = "none"
            edge_schemas[
                f"{edge_type[0]}→timing_path→{edge_type[2]}"
            ] = []
    if rc_present:
        if RC_COUPLING_EDGE_TYPE in data.edge_types:
            data[RC_COUPLING_EDGE_TYPE].edge_schema = []
            data[RC_COUPLING_EDGE_TYPE].edge_y_schema = (
                RC_COUPLING_EDGE_Y_COLUMNS
            )
            data[RC_COUPLING_EDGE_TYPE].edge_y_unit = ["pF"]
            data[RC_COUPLING_EDGE_TYPE].label_transform = "none"
            edge_schemas["net→rc_coupling→net"] = []
        for edge_type in RC_RESISTANCE_EDGE_TYPES:
            if edge_type not in data.edge_types:
                continue
            data[edge_type].edge_schema = []
            data[edge_type].edge_y_schema = RC_RESISTANCE_EDGE_Y_COLUMNS
            data[edge_type].edge_y_unit = ["ohm"]
            data[edge_type].label_transform = "none"
            edge_schemas[
                f"{edge_type[0]}→rc_resistance→{edge_type[2]}"
            ] = []
    timing_edge_names = [
        "|".join(edge_type)
        for edge_type in TIMING_EDGE_TYPES
        if edge_type in data.edge_types
    ]
    rc_coupling_edge_name = "|".join(RC_COUPLING_EDGE_TYPE)
    rc_resistance_edge_names = [
        "|".join(edge_type)
        for edge_type in RC_RESISTANCE_EDGE_TYPES
        if edge_type in data.edge_types
    ]
    rc_edge_names = [rc_coupling_edge_name, *rc_resistance_edge_names]
    task_edge_policy = {
        "congestion": {
            "include_optional": ["gate|congestion_geom|gate"],
            "exclude": [*timing_edge_names, *rc_edge_names],
        },
        "wirelength": {
            "exclude": [
                "gate|congestion_geom|gate",
                *timing_edge_names,
                *rc_edge_names,
            ],
        },
        "irdrop": {
            "exclude": [
                "gate|congestion_geom|gate",
                *timing_edge_names,
                *rc_edge_names,
            ],
        },
        "timing": {
            "include_optional": (
                timing_edge_names if timing_edges_present else []
            ),
            "exclude": [
                "gate|congestion_geom|gate",
                *([] if timing_edges_present else timing_edge_names),
                *rc_edge_names,
            ],
        },
        "ground_cap": {
            "exclude": [
                "gate|congestion_geom|gate",
                *timing_edge_names,
                *rc_edge_names,
            ],
        },
        "coupling_cap": {
            "include_optional": [rc_coupling_edge_name],
            "exclude": [
                "gate|congestion_geom|gate",
                *timing_edge_names,
                *rc_resistance_edge_names,
            ],
        },
        "effective_resistance": {
            "include_optional": rc_resistance_edge_names,
            "exclude": [
                "gate|congestion_geom|gate",
                *timing_edge_names,
                rc_coupling_edge_name,
            ],
        },
        "default": {
            "exclude": [
                "gate|congestion_geom|gate",
                *timing_edge_names,
                *rc_edge_names,
            ],
        },
    }
    provenance = {
        "config": str(config_path),
        "base_graph": str(base_path),
        "features": str(feature_dir),
        "labels": str(label_dir),
        "feature_profile": profile,
        "prediction_stage": prediction_stage,
        "feature_cutoff": str(metadata.get("feature_cutoff", "")),
        "feature_source": str(metadata.get("feature_source_path", "")),
        "encode_map": str(encode_map_path),
        "topology_stage": "post_synthesis",
        "coordinate_stage": str(
            metadata.get("coordinate_source_stage", "")
        ),
        "hpwl_stage": str(metadata.get("hpwl_source_stage", "")),
        "hpwl_source": str(metadata.get("hpwl_source_path", "")),
        "label_stage": str(
            tables["net_labels"][0].get(
                "wirelength_label_source_stage", "route/post-route"
            )
        ),
        "label_source": str(
            tables["net_labels"][0].get(
                "wirelength_label_source_path", ""
            )
        ),
        "irdrop_source": str(
            tables["gate_labels"][0].get(
                "irdrop_label_source_path", ""
            )
        ),
        "timing_source": str(
            tables["pin_timing"][0].get("label_source_path", "")
            if tables["pin_timing"] else ""
        ),
        "timing_edge_semantics": (
            "directed OpenSTA data paths; delay stored as edge_y, not edge_attr"
        ),
        "rc_source": str(
            tables["net_labels"][0].get(
                "ground_cap_label_source_path", ""
            )
        ),
        "rc_edge_semantics": (
            "Cc uses symmetric net virtual edges; Reff uses directed "
            "driver-pin to sink-pin virtual edges; labels are edge_y only"
        ),
        "label_transform": "none",
        "label_value_contract": (
            "raw physical values; preprocessing is deferred to model input"
        ),
        "congestion_feature_stage": str(
            metadata.get("congestion_feature_source_stage", "placement")
        ),
        "congestion_geom_edge_source": (
            "trusted gate origins for grid membership; centers for nearest selection"
        ),
        "congestion_geom_edge_reference": (
            "fixed 15xMetal3-pitch (2.1um/4200DBU), same-grid undirected "
            "nearest edges with maximum degree five; disabled until standard-cell "
            "placement has completed"
        ),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = out_path.with_suffix(".alignment.json")
    report = {
        "ok": True,
        "design": design,
        "feature_profile": profile,
        "nodes": {
            node_type: int(data[node_type].num_nodes)
            for node_type in data.node_types
        },
        "edges": {
            "|".join(edge_type): int(data[edge_type].num_edges)
            for edge_type in data.edge_types
        },
        "optional_task_edges": {
            "gate|congestion_geom|gate": geom_stats,
            **{
                "|".join(edge_type): {
                    "enabled": edge_type in data.edge_types,
                    "edge_count": (
                        int(data[edge_type].num_edges)
                        if edge_type in data.edge_types
                        else 0
                    ),
                    "labels_are_edge_y_not_input": True,
                }
                for edge_type in TIMING_EDGE_TYPES
            },
            rc_coupling_edge_name: {
                "enabled": RC_COUPLING_EDGE_TYPE in data.edge_types,
                "edge_count": (
                    int(data[RC_COUPLING_EDGE_TYPE].num_edges)
                    if RC_COUPLING_EDGE_TYPE in data.edge_types
                    else 0
                ),
                "labels_are_edge_y_not_input": True,
                "two_directions_per_physical_pair": True,
            },
            **{
                "|".join(edge_type): {
                    "enabled": edge_type in data.edge_types,
                    "edge_count": (
                        int(data[edge_type].num_edges)
                        if edge_type in data.edge_types
                        else 0
                    ),
                    "labels_are_edge_y_not_input": True,
                }
                for edge_type in RC_RESISTANCE_EDGE_TYPES
            },
        },
        "alignment": alignment,
        "finite_labels": {
            "gate_congestion": int(torch.isfinite(data["gate"].y[:, 0]).sum()),
            "gate_irdrop": int(torch.isfinite(data["gate"].y[:, 1]).sum()),
            "net_wirelength": int(
                torch.isfinite(data["net"].y[:, 0]).sum()
            ),
            "net_ground_cap": int(
                torch.isfinite(data["net"].y[:, 1]).sum()
            ),
            "pin_setup_slack": int(
                torch.isfinite(data["pin"].y[:, 0]).sum()
            ),
            "pin_hold_slack": int(
                torch.isfinite(data["pin"].y[:, 1]).sum()
            ),
            "iopin_setup_slack": int(
                torch.isfinite(data["io_pin"].y[:, 0]).sum()
            ),
            "iopin_hold_slack": int(
                torch.isfinite(data["io_pin"].y[:, 1]).sum()
            ),
        },
        "runtime_views": {
            "helper": "../archive/graph_views.py::to_homogeneous_view",
            "pipeline_dependency": False,
            "duplicated_tensors_stored": False,
        },
        "encoding_maps": {
            map_name: len(mapping)
            for map_name, mapping in encoding_maps.items()
        },
        "labels": {
            "gate": {
                "schema": ["cell_congestion", "ir_drop_mV"],
                "unit": ["dimensionless", "mV"],
                "transform": "none",
                "valid_mask": "gate.y_valid_mask from CSV valid columns",
            },
            "net": {
                "schema": ["routed_wirelength_um", "ground_cap_pF"],
                "unit": ["um", "pF"],
                "transform": "none",
                "valid_mask": "net.y_valid_mask from CSV valid columns",
            },
            "pin": {
                "schema": ["setup_slack_ns", "hold_slack_ns"],
                "unit": ["ns", "ns"],
                "transform": "none",
                "valid_mask": "pin.y_valid_mask from CSV valid columns",
            },
            "io_pin": {
                "schema": ["setup_slack_ns", "hold_slack_ns"],
                "unit": ["ns", "ns"],
                "transform": "none",
                "valid_mask": "io_pin.y_valid_mask from CSV valid columns",
            },
            "timing_edges": {
                "enabled": timing_edges_present,
                "schema": TIMING_EDGE_Y_COLUMNS,
                "unit": ["ns", "ns"],
                "storage": "edge_y with edge_y_mask",
            },
            "rc_coupling_edges": {
                "schema": RC_COUPLING_EDGE_Y_COLUMNS,
                "unit": ["pF"],
                "storage": "edge_y with edge_y_mask",
            },
            "rc_resistance_edges": {
                "schema": RC_RESISTANCE_EDGE_Y_COLUMNS,
                "unit": ["ohm"],
                "storage": "edge_y with edge_y_mask",
            },
        },
        "leakage_warning": data.leakage_warning,
    }
    atomic_write_json(report_path, report)
    metadata_path = out_path.with_suffix(".metadata.json")
    sidecar = {
        "schema": "r2g2_heterograph_metadata_v1",
        "data_contract_version": data.data_contract_version,
        "feature_availability_contract": data.feature_availability_contract,
        "auxiliary_edge_availability_contract": (
            data.auxiliary_edge_availability_contract
        ),
        "shared_label_contract": data.shared_label_contract,
        "design_name": design,
        "feature_profile": profile,
        "schemas": {
            "node_x": {
                node_type: [*columns, "graph_id"]
                for node_type, columns in schemas.items()
            },
            "node_y": {
                "gate": ["cell_congestion", "ir_drop_mV"],
                "net": ["routed_wirelength_um", "ground_cap_pF"],
                "io_pin": ["setup_slack_ns", "hold_slack_ns"],
                "pin": ["setup_slack_ns", "hold_slack_ns"],
            },
            "node_y_valid_mask": {
                "gate": ["congestion_valid", "irdrop_valid"],
                "net": ["wirelength_valid", "ground_cap_valid"],
                "io_pin": ["setup_valid", "hold_valid"],
                "pin": ["setup_valid", "hold_valid"],
            },
            "edge_attr": edge_schemas,
            "edge_y": {
                **{
                    f"{edge_type[0]}→timing_path→{edge_type[2]}":
                        TIMING_EDGE_Y_COLUMNS
                    for edge_type in TIMING_EDGE_TYPES
                    if edge_type in data.edge_types
                },
                "net→rc_coupling→net": RC_COUPLING_EDGE_Y_COLUMNS,
                **{
                    f"{edge_type[0]}→rc_resistance→{edge_type[2]}":
                        RC_RESISTANCE_EDGE_Y_COLUMNS
                    for edge_type in RC_RESISTANCE_EDGE_TYPES
                    if edge_type in data.edge_types
                },
            },
            "global_features": global_columns,
        },
        "runtime_views": {
            "helper": "../archive/graph_views.py::to_homogeneous_view",
            "task_filter": "../archive/graph_views.py::apply_task_view",
            "pipeline_dependency": False,
            "duplicated_tensors_stored": False,
            "homogeneous_target_schema": [
                "gate:cell_congestion",
                "gate:ir_drop_mV",
                "net:routed_wirelength_um",
                "net:ground_cap_pF",
                "pin:setup_slack_ns",
                "pin:hold_slack_ns",
                "io_pin:setup_slack_ns",
                "io_pin:hold_slack_ns",
            ],
        },
        "label_contract": {
            "raw_physical_values": True,
            "normalization_applied": False,
            "filtering_applied": False,
            "data_split_applied": False,
            "valid_mask_semantics": (
                "source_validity_from_label_csv_not_training_selection"
            ),
            "gate": {
                "schema": ["cell_congestion", "ir_drop_mV"],
                "unit": ["dimensionless", "mV"],
                "transform": "none",
                "valid_mask_store": "gate.y_valid_mask",
            },
            "net": {
                "schema": ["routed_wirelength_um", "ground_cap_pF"],
                "unit": ["um", "pF"],
                "transform": "none",
                "valid_mask_store": "net.y_valid_mask",
            },
            "pin": {
                "schema": ["setup_slack_ns", "hold_slack_ns"],
                "unit": ["ns", "ns"],
                "transform": "none",
                "valid_mask_store": "pin.y_valid_mask",
            },
            "io_pin": {
                "schema": ["setup_slack_ns", "hold_slack_ns"],
                "unit": ["ns", "ns"],
                "transform": "none",
                "valid_mask_store": "io_pin.y_valid_mask",
            },
            "timing_edge": {
                "schema": TIMING_EDGE_Y_COLUMNS,
                "unit": ["ns", "ns"],
                "storage": "edge_y with edge_y_mask",
                "is_model_input": False,
            },
            "rc_coupling_edge": {
                "schema": RC_COUPLING_EDGE_Y_COLUMNS,
                "unit": ["pF"],
                "storage": "edge_y with edge_y_mask",
                "is_model_input": False,
                "topology_scope": "positive extracted pairs only",
            },
            "rc_resistance_edge": {
                "schema": RC_RESISTANCE_EDGE_Y_COLUMNS,
                "unit": ["ohm"],
                "storage": "edge_y with edge_y_mask",
                "is_model_input": False,
            },
        },
        "encoding": {
            "csv": str(encode_map_path),
            "sha256": data.encode_map_sha256,
            "selected_rows": len(encoding_rows),
            "map_sizes": {
                map_name: len(mapping)
                for map_name, mapping in encoding_maps.items()
            },
        },
        "task_edge_policy": task_edge_policy,
        "congestion_geom_stats": geom_stats,
        "provenance": provenance,
        "alignment": alignment,
        "alignment_report": report_path.name,
        "leakage_warning": data.leakage_warning,
    }
    atomic_write_json(metadata_path, sidecar)
    data.metadata_file = metadata_path.name
    data.metadata_sha256 = sha256_file(metadata_path)
    data.alignment_file = report_path.name
    atomic_torch_save(data, out_path)
    print(f"[assemble] saved: {out_path}")
    print(f"[assemble] metadata: {metadata_path}")
    print(f"[assemble] report: {report_path}")
    print(f"[assemble] nodes={report['nodes']} edges={report['edges']}")
    print(f"[assemble] finite_labels={report['finite_labels']}")


if __name__ == "__main__":
    main()
