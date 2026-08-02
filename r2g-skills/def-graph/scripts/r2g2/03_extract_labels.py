#!/usr/bin/env python3
"""阶段3：统一提取线长、拥塞、IR Drop、时序和RC原始标签。

这些标签都属于 route/post-route 监督信号。正式CSV按最终节点/边位置合并：

* gate_con_IR.csv：Gate congestion与IRDrop；
* net_wirelength_Cg.csv：Net routed wirelength与Cg；
* pin_timing/iopin_timing：OpenSTA setup/hold slack；
* edges_timing_path.csv：可选的OpenSTA报告路径审计表，默认不生成、不参与模型；
* edges_net_net_Cc.csv与edges_pin_pin_Reff.csv：同样本Raw后布线SPEF RC任务边。

OpenRCX三表和旧IRDrop CSV只能作为一致性参考；正式标签始终从
DEF/LEF/SPEF/SP/RPT原始文件重新计算。

输出使用与基础图相同的 net_name/inst_name。标签始终保存物理原值，不做log、
归一化、标准化或截断；所有变换留到模型输入数据处理阶段。

Route阶段拆分的小网通过稳定端点及新增buffer链反标到Canonical base Net；
wirelength/Cg按其全部物理小网求和，Cc/Reff同样先映射再对齐。拥塞统一使用
15×Metal3 pitch=2.1um（Nangate45为4200DBU）的预先可知网格。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import importlib.util
import json
import math
import re
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import torch

SCRIPT_DIR = Path(__file__).resolve().parent
FAST_ROUTE_GRID_TRACKS = 15
METAL3_PITCH_UM = 0.14
FIXED_CONGESTION_GRID_UM = FAST_ROUTE_GRID_TRACKS * METAL3_PITCH_UM


def _require_scipy() -> dict[str, Any]:
    """按需导入SciPy稀疏求解器。

    r2g-skills delta vs upstream R2G2.0: 上游在模块顶层``import scipy``。
    SciPy只被IR-drop的KCL求解用到, 顶层导入会让"没装SciPy"变成整个标签阶段
    不可运行 —— 连不依赖SciPy的wirelength/congestion/timing/RC四类标签都拿
    不到。这里改成惰性导入, 缺SciPy时只让IR-drop这一列fail-soft(与本skill
    "缺输入只降级一列, 不中止其余"的契约一致), 并给出可执行的HINT。
    """

    try:
        from scipy.sparse import coo_matrix
        from scipy.sparse.linalg import (
            LinearOperator,
            MatrixRankWarning,
            cg,
            spsolve,
        )
    except ImportError as error:  # pragma: no cover - environment dependent
        raise ImportError(
            "IR-drop标签需要SciPy稀疏求解器, 当前解释器没有scipy。\n"
            "HINT: 用 $R2G_GRAPH_PYTHON -m pip install scipy 安装, "
            "或加 --skip-irdrop 只跳过IR-drop这一列。"
        ) from error
    return {
        "coo_matrix": coo_matrix,
        "LinearOperator": LinearOperator,
        "MatrixRankWarning": MatrixRankWarning,
        "cg": cg,
        "spsolve": spsolve,
    }


def load_feature_module() -> Any:
    """复用阶段2的DEF解析，保证特征、标签和快照看到同一物理拓扑。"""

    path = SCRIPT_DIR / "02_extract_features.py"
    spec = importlib.util.spec_from_file_location(
        "r2g2_four_stage_features_for_labels", path
    )
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ===== 阶段3内置：RC原始SPEF解析、CSV核验与基础图对齐 =====

def _read_csv(path: Path, required_columns: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = required_columns - columns
        if missing:
            raise ValueError(f"{path} 缺少字段: {sorted(missing)}")
        return list(reader)


def _write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    """原子写CSV，避免长时间标签提取被中断后留下半张表。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """原子写标签统计sidecar。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _raw_nonnegative(row: dict[str, str], column: str, table: Path) -> float:
    try:
        value = float(row[column])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{table} 的{column}不是数值: {row}") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{table} 的{column}必须是有限非负原值: {row}")
    return value

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _endpoint(
    raw_name: str,
    pin_keys: set[tuple[str, str]],
    io_keys: set[str],
) -> tuple[str, tuple[str, str] | str] | None:
    name = canonical_name(raw_name)
    if name in io_keys:
        return "io_pin", name
    if ":" not in name:
        return None
    inst_name, pin_name = name.rsplit(":", 1)
    key = (inst_name, pin_name)
    return ("pin", key) if key in pin_keys else None

def _same_value(previous: float, current: float) -> bool:
    return math.isclose(previous, current, rel_tol=1e-9, abs_tol=1e-15)

_STAR_REF_RE = re.compile(r"^\*(\d+)(.*)$")

_CAP_TO_PF = {
    "F": 1e12,
    "UF": 1e6,
    "NF": 1e3,
    "PF": 1.0,
    "FF": 1e-3,
    "AF": 1e-6,
}

_RES_TO_OHM = {
    "OHM": 1.0,
    "KOHM": 1e3,
    "MOHM": 1e6,
}

def _resolve_spef_name(token: str, name_map: dict[str, str]) -> str:
    match = _STAR_REF_RE.match(token)
    if not match:
        return canonical_name(token)
    key, suffix = match.groups()
    return canonical_name(name_map.get(key, token) + suffix)

def _base_spef_token(token: str) -> str:
    return token.split(":", 1)[0] if token.startswith("*") else token

def _spef_number(token: str, corner: str) -> float:
    """读取SPEF标量或min:typ:max三元组，非法数值直接报错。"""

    values = token.split(":")
    if len(values) == 1:
        selected = values[0]
    elif len(values) == 3:
        selected = values[{"min": 0, "typ": 1, "max": 2}[corner]]
    else:
        raise ValueError(f"不支持的SPEF数值格式: {token!r}")
    value = float(selected)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"SPEF RC数值必须有限且非负: {token!r}")
    return value

def _spef_unit_scale(
    line: str,
    expected_prefix: str,
    unit_map: dict[str, float],
) -> tuple[float, str]:
    parts = line.split()
    if len(parts) != 3 or parts[0] != expected_prefix:
        raise ValueError(f"非法SPEF单位行: {line!r}")
    factor = float(parts[1])
    unit = parts[2].upper()
    if not math.isfinite(factor) or factor <= 0 or unit not in unit_map:
        raise ValueError(f"不支持的SPEF单位: {line!r}")
    return factor * unit_map[unit], f"{parts[1]} {unit}"

class _DSU:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, first: str, second: str) -> None:
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root != second_root:
            self.parent[second_root] = first_root

def _driver_sink_resistance_rows(
    *,
    net_name: str,
    connections: list[dict[str, str]],
    resistance_edges: list[tuple[str, str, float]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """在单Net RC-tree上计算每个Driver到Sink的串联路径电阻。"""

    if not resistance_edges:
        return [], {
            "zero_ohm_segments": 0,
            "parallel_segments": 0,
            "fallback_driver_nets": 0,
            "fallback_sink_nets": 0,
            "disconnected_pairs": 0,
        }

    dsu = _DSU()
    for connection in connections:
        dsu.find(connection["token"])
    zero_ohm_segments = 0
    for first, second, resistance in resistance_edges:
        dsu.find(first)
        dsu.find(second)
        if resistance == 0:
            zero_ohm_segments += 1
            dsu.union(first, second)

    root_to_id: dict[str, int] = {}

    def node_id(token: str) -> int:
        root = dsu.find(token)
        if root not in root_to_id:
            root_to_id[root] = len(root_to_id)
        return root_to_id[root]

    adjacency: dict[int, dict[int, float]] = defaultdict(dict)
    parallel_segments = 0
    for first, second, resistance in resistance_edges:
        source = node_id(first)
        target = node_id(second)
        adjacency.setdefault(source, {})
        adjacency.setdefault(target, {})
        if source == target or resistance == 0:
            continue
        if target in adjacency[source]:
            parallel_segments += 1
            # OpenRCX正常输出是RC tree；异常并行段取更小显式路径并写入审计。
            resistance = min(resistance, adjacency[source][target])
        adjacency[source][target] = resistance
        adjacency[target][source] = resistance

    drivers = [
        connection
        for connection in connections
        if (
            connection["kind"] == "*I"
            and connection["direction"] in {"O", "B"}
        )
        or (
            connection["kind"] == "*P"
            and connection["direction"] == "I"
        )
    ]
    sinks = [
        connection
        for connection in connections
        if (
            connection["kind"] == "*I"
            and connection["direction"] == "I"
        )
        or (
            connection["kind"] == "*P"
            and connection["direction"] == "O"
        )
    ]
    fallback_driver_nets = 0
    fallback_sink_nets = 0
    if not drivers and connections:
        drivers = [connections[0]]
        fallback_driver_nets = 1
    if not sinks and len(connections) > 1:
        driver_id = node_id(drivers[0]["token"])
        sinks = [
            connection
            for connection in connections
            if node_id(connection["token"]) != driver_id
        ]
        fallback_sink_nets = 1

    rows: list[dict[str, Any]] = []
    disconnected_pairs = 0
    for driver in drivers:
        source = node_id(driver["token"])
        distances = {source: 0.0}
        queue = [(0.0, source)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances.get(node):
                continue
            for neighbor, resistance in adjacency.get(node, {}).items():
                candidate = distance + resistance
                if candidate < distances.get(neighbor, float("inf")):
                    distances[neighbor] = candidate
                    heapq.heappush(queue, (candidate, neighbor))
        for sink in sinks:
            target = node_id(sink["token"])
            value = distances.get(target)
            if value is None:
                disconnected_pairs += 1
                continue
            rows.append(
                {
                    "Net": net_name,
                    "DriverPin": driver["name"],
                    "SinkPin": sink["name"],
                    "EffectiveResistance_ohm": value,
                    "label": value,
                }
            )
    return rows, {
        "zero_ohm_segments": zero_ohm_segments,
        "parallel_segments": parallel_segments,
        "fallback_driver_nets": fallback_driver_nets,
        "fallback_sink_nets": fallback_sink_nets,
        "disconnected_pairs": disconnected_pairs,
    }

def extract_rc_csv_from_spef(
    *,
    spef_path: Path,
    output_dir: Path,
    design: str,
    corner: str = "typ",
    allow_truncated_tail: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """流式解析同样本Raw后布线SPEF并生成三张OpenRCX兼容原始CSV。

    输出统一转换为pF和ohm。SPEF若使用min:typ:max三元值，按``corner``选取。
    ``allow_truncated_tail``只允许恢复文件末尾唯一一个未闭合的``*D_NET``：
    该Net的全部临时记录会被丢弃并在summary中登记，前面闭合Net不受影响。
    中间损坏、闭合Net内错误或多个未闭合Net仍然立即报错。
    """

    if corner not in {"min", "typ", "max"}:
        raise ValueError(f"rc_spef_corner必须是min/typ/max: {corner!r}")
    spef_path = spef_path.resolve()
    if not spef_path.is_file():
        raise FileNotFoundError(spef_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    name_map: dict[str, str] = {}
    token_to_net: dict[str, str] = {}
    dnet_token_to_net: dict[str, str] = {}
    raw_couplings: list[tuple[str, str, str, float]] = []
    ground_rows: list[dict[str, Any]] = []
    resistance_rows: list[dict[str, Any]] = []
    cap_scale: float | None = None
    res_scale: float | None = None
    cap_unit = ""
    res_unit = ""
    in_name_map = False
    section = ""
    current_net_name = ""
    connections: list[dict[str, str]] = []
    resistance_edges: list[tuple[str, str, float]] = []
    ground_cap_pF = 0.0
    ground_segments = 0
    net_count = 0
    parser_stats = defaultdict(int)
    current_dnet_token = ""
    current_net_start_line = 0
    current_coupling_start = 0
    current_mapped_tokens: set[str] = set()
    truncated_tail_recovery: dict[str, Any] = {
        "enabled": bool(allow_truncated_tail),
        "applied": False,
        "dropped_net_count": 0,
    }

    def map_token(token: str) -> None:
        if current_net_name:
            keys = (token, _base_spef_token(token))
            for key in keys:
                token_to_net[key] = current_net_name
                current_mapped_tokens.add(key)

    def finalize_net() -> None:
        nonlocal current_net_name, connections, resistance_edges
        nonlocal ground_cap_pF, ground_segments, net_count
        nonlocal current_dnet_token, current_net_start_line
        nonlocal current_coupling_start, current_mapped_tokens
        if not current_net_name:
            return
        for connection in connections:
            map_token(connection["token"])
        for first, second, _ in resistance_edges:
            map_token(first)
            map_token(second)
        ground_rows.append(
            {
                "Design": design,
                "Net": current_net_name,
                "GroundCap_pF": ground_cap_pF,
                "label": ground_cap_pF,
            }
        )
        net_resistance_rows, stats = _driver_sink_resistance_rows(
            net_name=current_net_name,
            connections=connections,
            resistance_edges=resistance_edges,
        )
        for row in net_resistance_rows:
            row["Design"] = design
        resistance_rows.extend(net_resistance_rows)
        for key, value in stats.items():
            parser_stats[key] += value
        parser_stats["ground_cap_segments"] += ground_segments
        parser_stats["resistance_segments"] += len(resistance_edges)
        net_count += 1
        current_net_name = ""
        current_dnet_token = ""
        current_net_start_line = 0
        current_coupling_start = len(raw_couplings)
        current_mapped_tokens = set()
        connections = []
        resistance_edges = []
        ground_cap_pF = 0.0
        ground_segments = 0

    def discard_truncated_tail(
        *,
        reason: str,
        error_line: int,
        raw_line: str,
    ) -> None:
        nonlocal current_net_name, connections, resistance_edges
        nonlocal ground_cap_pF, ground_segments
        nonlocal current_dnet_token, current_net_start_line
        nonlocal current_coupling_start, current_mapped_tokens, section
        if not current_net_name:
            raise ValueError("尾部恢复被请求，但当前没有打开的*D_NET")
        dropped_couplings = len(raw_couplings) - current_coupling_start
        del raw_couplings[current_coupling_start:]
        parser_stats["coupling_cap_segments"] -= dropped_couplings
        for key in current_mapped_tokens:
            if token_to_net.get(key) == current_net_name:
                token_to_net.pop(key, None)
        if (
            current_dnet_token
            and dnet_token_to_net.get(current_dnet_token)
            == current_net_name
        ):
            dnet_token_to_net.pop(current_dnet_token, None)
        truncated_tail_recovery.update(
            {
                "applied": True,
                "dropped_net_count": 1,
                "dropped_net_name": current_net_name,
                "dropped_dnet_token": current_dnet_token,
                "dropped_net_start_line": current_net_start_line,
                "error_line": error_line,
                "last_raw_line": raw_line.rstrip("\r\n"),
                "reason": reason,
                "discarded_connection_rows": len(connections),
                "discarded_ground_cap_segments": ground_segments,
                "discarded_coupling_cap_segments": dropped_couplings,
                "discarded_resistance_segments": len(resistance_edges),
                "recovery_policy": (
                    "discard_exactly_one_unclosed_final_dnet"
                ),
            }
        )
        parser_stats["truncated_tail_dropped_nets"] += 1
        current_net_name = ""
        current_dnet_token = ""
        current_net_start_line = 0
        current_coupling_start = len(raw_couplings)
        current_mapped_tokens = set()
        connections = []
        resistance_edges = []
        ground_cap_pF = 0.0
        ground_segments = 0
        section = ""

    recovered_at_eof = False
    line_number = 0
    raw = ""
    with spef_path.open("r", encoding="utf-8", errors="strict") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                if line.startswith("*C_UNIT"):
                    cap_scale, cap_unit = _spef_unit_scale(
                        line, "*C_UNIT", _CAP_TO_PF
                    )
                    continue
                if line.startswith("*R_UNIT"):
                    res_scale, res_unit = _spef_unit_scale(
                        line, "*R_UNIT", _RES_TO_OHM
                    )
                    continue
                if line == "*NAME_MAP":
                    in_name_map = True
                    continue
                if in_name_map:
                    if line.startswith("*PORTS") or line.startswith("*D_NET"):
                        in_name_map = False
                    else:
                        fields = line.split(maxsplit=1)
                        if len(fields) == 2 and fields[0].startswith("*"):
                            name_map[fields[0][1:]] = fields[1]
                        continue
                if line.startswith("*D_NET"):
                    if current_net_name:
                        raise ValueError(
                            "遇到新的*D_NET，但前一个Net没有*END"
                        )
                    fields = line.split()
                    if len(fields) < 3:
                        raise ValueError("*D_NET字段不足")
                    current_net_name = _resolve_spef_name(fields[1], name_map)
                    current_dnet_token = fields[1]
                    current_net_start_line = line_number
                    current_coupling_start = len(raw_couplings)
                    current_mapped_tokens = set()
                    dnet_token_to_net[fields[1]] = current_net_name
                    section = ""
                    continue
                if not current_net_name:
                    continue
                if line == "*CONN":
                    section = "CONN"
                    continue
                if line == "*CAP":
                    if cap_scale is None:
                        raise ValueError("*CAP之前没有有效*C_UNIT")
                    section = "CAP"
                    continue
                if line == "*RES":
                    if res_scale is None:
                        raise ValueError("*RES之前没有有效*R_UNIT")
                    section = "RES"
                    continue
                if line == "*END":
                    finalize_net()
                    section = ""
                    continue

                fields = line.split()
                if section == "CONN" and fields[0] in {"*I", "*P"}:
                    if len(fields) < 3:
                        raise ValueError("*CONN记录字段不足")
                    connections.append(
                        {
                            "kind": fields[0],
                            "token": fields[1],
                            "name": _resolve_spef_name(fields[1], name_map),
                            "direction": fields[2],
                        }
                    )
                elif section == "CAP":
                    if len(fields) == 3:
                        value = _spef_number(fields[2], corner) * cap_scale
                        ground_cap_pF += value
                        ground_segments += 1
                        map_token(fields[1])
                    elif len(fields) >= 4:
                        value = _spef_number(fields[3], corner) * cap_scale
                        raw_couplings.append(
                            (
                                current_net_name,
                                fields[1],
                                fields[2],
                                value,
                            )
                        )
                        parser_stats["coupling_cap_segments"] += 1
                    else:
                        raise ValueError("*CAP记录字段不足")
                elif section == "RES":
                    if len(fields) < 4:
                        raise ValueError("*RES记录字段不足")
                    value = _spef_number(fields[3], corner) * res_scale
                    resistance_edges.append((fields[1], fields[2], value))
            except (KeyError, TypeError, ValueError) as error:
                # 无换行符的物理末行必然是EOF。仅在显式允许且当前只打开
                # 一个尾部D_NET时丢弃该Net；任何中间错误继续严格失败。
                if (
                    allow_truncated_tail
                    and current_net_name
                    and not raw.endswith(("\n", "\r"))
                    and not truncated_tail_recovery["applied"]
                ):
                    discard_truncated_tail(
                        reason=f"{type(error).__name__}: {error}",
                        error_line=line_number,
                        raw_line=raw,
                    )
                    recovered_at_eof = True
                    break
                raise ValueError(
                    f"{spef_path}:{line_number}: {error}; line={line!r}"
                ) from error
    if current_net_name:
        if allow_truncated_tail and not truncated_tail_recovery["applied"]:
            discard_truncated_tail(
                reason="EOF before required *END",
                error_line=line_number,
                raw_line=raw,
            )
            recovered_at_eof = True
        else:
            raise ValueError(
                f"{spef_path}:{line_number}: EOF前Net "
                f"{current_net_name!r}缺少*END"
            )
    if recovered_at_eof:
        print(
            "[label][rc] WARNING: recovered truncated SPEF tail by "
            f"dropping unclosed net "
            f"{truncated_tail_recovery.get('dropped_net_name')!r}; "
            "its Cg/Cc/Reff remain invalid",
            flush=True,
        )
    if cap_scale is None or res_scale is None:
        raise ValueError(f"{spef_path} 缺少有效*C_UNIT或*R_UNIT")

    def coupling_node_net(token: str) -> str | None:
        exact = token_to_net.get(token)
        if exact:
            return exact
        base = _base_spef_token(token)
        suffix = token.split(":", 1)[1] if ":" in token else ""
        # 数字后缀是SPEF Net内部RC节点；优先使用*D_NET token映射。
        if suffix.isdigit() and base in dnet_token_to_net:
            return dnet_token_to_net[base]
        # 字母后缀通常是instance pin；必须使用其在*CONN/RC网络中的实际Net。
        return token_to_net.get(base) or (
            dnet_token_to_net.get(base) if not suffix else None
        )

    coupling_values: dict[tuple[str, str], float] = defaultdict(float)
    unresolved_coupling_segments = 0
    zero_coupling_segments = 0
    for owner_net, first, second, value in raw_couplings:
        if value == 0:
            zero_coupling_segments += 1
            continue
        first_net = coupling_node_net(first)
        second_net = coupling_node_net(second)
        if first_net == owner_net and second_net and second_net != owner_net:
            other_net = second_net
        elif second_net == owner_net and first_net and first_net != owner_net:
            other_net = first_net
        else:
            other_net = second_net or first_net
        if not other_net or other_net == owner_net:
            unresolved_coupling_segments += 1
            continue
        pair = tuple(sorted((owner_net, other_net)))
        coupling_values[pair] += value

    coupling_rows = [
        {
            "Design": design,
            "Net1": first,
            "Net2": second,
            "CouplingCap_pF": value,
            "label": value,
        }
        for (first, second), value in sorted(coupling_values.items())
    ]
    ground_rows.sort(key=lambda row: canonical_name(str(row["Net"])))
    resistance_rows.sort(
        key=lambda row: (
            canonical_name(str(row["Net"])),
            canonical_name(str(row["DriverPin"])),
            canonical_name(str(row["SinkPin"])),
        )
    )

    tables_and_fields = {
        "ground_cap.csv": (
            ["Design", "Net", "GroundCap_pF", "label"],
            ground_rows,
        ),
        "net_coupling.csv": (
            ["Design", "Net1", "Net2", "CouplingCap_pF", "label"],
            coupling_rows,
        ),
        "pin_net_resistance.csv": (
            [
                "Design",
                "Net",
                "DriverPin",
                "SinkPin",
                "EffectiveResistance_ohm",
                "label",
            ],
            resistance_rows,
        ),
    }
    for filename, (fieldnames, rows) in tables_and_fields.items():
        _write_csv(output_dir / filename, fieldnames, rows)

    summary = {
        "schema": "r2g2_spef_rc_extraction_v1",
        "source_spef": str(spef_path),
        "source_spef_sha256": _sha256(spef_path),
        "source_design": design,
        "corner": corner,
        "input_cap_unit": cap_unit,
        "input_res_unit": res_unit,
        "output_cap_unit": "pF",
        "output_res_unit": "ohm",
        "net_rows": len(ground_rows),
        "coupling_pair_rows": len(coupling_rows),
        "resistance_edge_rows": len(resistance_rows),
        "unresolved_coupling_segments": unresolved_coupling_segments,
        "zero_coupling_segments_skipped": zero_coupling_segments,
        "parser_stats": dict(sorted(parser_stats.items())),
        "input_completeness": (
            "complete_except_one_discarded_unclosed_tail_dnet"
            if truncated_tail_recovery["applied"]
            else "complete"
        ),
        "truncated_tail_recovery": truncated_tail_recovery,
        "raw_physical_values": True,
        "label_transform": "none",
    }
    return output_dir, summary

def compare_rc_csv_directories(
    generated_dir: Path,
    reference_dir: Path,
) -> dict[str, Any]:
    """按规范化实体键比较两组三表，不受CSV行序和SPEF转义影响。"""

    specs = {
        "ground_cap": (
            "ground_cap.csv",
            ("Net",),
            "GroundCap_pF",
        ),
        "net_coupling": (
            "net_coupling.csv",
            ("Net1", "Net2"),
            "CouplingCap_pF",
        ),
        "pin_net_resistance": (
            "pin_net_resistance.csv",
            ("Net", "DriverPin", "SinkPin"),
            "EffectiveResistance_ohm",
        ),
    }
    tables: dict[str, Any] = {}
    all_exact = True
    for name, (filename, key_columns, value_column) in specs.items():
        generated_path = generated_dir / filename
        reference_path = reference_dir / filename

        def indexed(path: Path) -> dict[tuple[str, ...], float]:
            rows = _read_csv(path, {*key_columns, value_column})
            result: dict[tuple[str, ...], float] = {}
            for row in rows:
                key = tuple(canonical_name(row[column]) for column in key_columns)
                value = _raw_nonnegative(row, value_column, path)
                if key in result:
                    raise ValueError(f"{path} 存在重复RC实体键: {key}")
                result[key] = value
            return result

        generated = indexed(generated_path)
        reference = indexed(reference_path)
        common = generated.keys() & reference.keys()
        mismatched = [
            key
            for key in common
            if not math.isclose(
                generated[key],
                reference[key],
                rel_tol=1e-12,
                abs_tol=1e-15,
            )
        ]
        missing = reference.keys() - generated.keys()
        extra = generated.keys() - reference.keys()
        exact = not missing and not extra and not mismatched
        all_exact = all_exact and exact
        tables[name] = {
            "exact": exact,
            "generated_rows": len(generated),
            "reference_rows": len(reference),
            "missing_keys": len(missing),
            "extra_keys": len(extra),
            "value_mismatches": len(mismatched),
            "missing_examples": [list(key) for key in list(missing)[:5]],
            "extra_examples": [list(key) for key in list(extra)[:5]],
            "mismatch_examples": [list(key) for key in mismatched[:5]],
            "generated_sha256": _sha256(generated_path),
            "reference_sha256": _sha256(reference_path),
        }
    return {
        "exact": all_exact,
        "generated_dir": str(generated_dir.resolve()),
        "reference_dir": str(reference_dir.resolve()),
        "comparison_ignores_row_order_and_name_escaping": True,
        "tables": tables,
    }

def build_aligned_rc_tables(
    *,
    design: str,
    net_names: list[str],
    pin_keys: list[tuple[str, str]],
    io_keys: list[str],
    source_dir: Path,
    stage_to_base: dict[str, str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """将Route小网RC聚合/反标到Canonical base Net。"""

    source_dir = source_dir.resolve()
    ground_path = source_dir / "ground_cap.csv"
    coupling_path = source_dir / "net_coupling.csv"
    resistance_path = source_dir / "pin_net_resistance.csv"
    ground_source = _read_csv(
        ground_path, {"Design", "Net", "GroundCap_pF"}
    )
    coupling_source = _read_csv(
        coupling_path, {"Design", "Net1", "Net2", "CouplingCap_pF"}
    )
    resistance_source = _read_csv(
        resistance_path,
        {
            "Design",
            "Net",
            "DriverPin",
            "SinkPin",
            "EffectiveResistance_ohm",
        },
    )

    net_order = {canonical_name(name): index for index, name in enumerate(net_names)}
    if len(net_order) != len(net_names):
        raise ValueError("基础图Net名称在canonical_name后不唯一")
    pin_set = {
        (canonical_name(inst_name), canonical_name(pin_name))
        for inst_name, pin_name in pin_keys
    }
    io_set = {canonical_name(name) for name in io_keys}
    canonical_stage_to_base = {
        canonical_name(stage_net): canonical_name(base_net)
        for stage_net, base_net in stage_to_base.items()
    }

    def mapped_base_net(route_net: str) -> str | None:
        mapped = canonical_stage_to_base.get(canonical_name(route_net))
        return mapped if mapped in net_order else None

    ground_values: dict[str, float] = defaultdict(float)
    ground_extra = 0
    for row in ground_source:
        net_name = mapped_base_net(row["Net"])
        value = _raw_nonnegative(row, "GroundCap_pF", ground_path)
        if net_name is None:
            ground_extra += 1
            continue
        ground_values[net_name] += value

    ground_rows: list[dict[str, Any]] = []
    for net_name in net_names:
        key = canonical_name(net_name)
        valid = int(key in ground_values)
        ground_rows.append(
            {
                "graph_id": design,
                "net_name": net_name,
                "ground_cap_pF": ground_values.get(key, float("nan")),
                "valid": valid,
                "label_unit": "pF",
                "label_transform": "none",
                "label_definition": "sum of SPEF ground capacitances on the net",
                "label_source_stage": "post_route_openrcx",
                "label_source_path": str(ground_path),
                "source_design": (
                    ground_source[0].get("Design", "") if ground_source else ""
                ),
            }
        )

    coupling_values: dict[tuple[str, str], float] = defaultdict(float)
    coupling_source_pairs: set[tuple[str, str]] = set()
    coupling_unaligned = 0
    coupling_self = 0
    for row in coupling_source:
        net1 = mapped_base_net(row["Net1"])
        net2 = mapped_base_net(row["Net2"])
        value = _raw_nonnegative(row, "CouplingCap_pF", coupling_path)
        if net1 is None or net2 is None:
            coupling_unaligned += 1
            continue
        if net1 == net2:
            coupling_self += 1
            continue
        pair = (
            (net1, net2)
            if net_order[net1] < net_order[net2]
            else (net2, net1)
        )
        coupling_values[pair] += value
        coupling_source_pairs.add(pair)

    coupling_rows = [
        {
            "graph_id": design,
            "net1_name": net1,
            "net2_name": net2,
            "coupling_cap_pF": coupling_values[(net1, net2)],
            "valid": 1,
            "label_unit": "pF",
            "label_transform": "none",
            "label_definition": (
                "sum of SPEF coupling capacitance segments for an unordered net pair"
            ),
            "label_source_stage": "post_route_openrcx",
            "label_source_path": str(coupling_path),
            "source_design": (
                coupling_source[0].get("Design", "") if coupling_source else ""
            ),
        }
        for net1, net2 in sorted(
            coupling_values,
            key=lambda pair: (net_order[pair[0]], net_order[pair[1]]),
        )
    ]

    resistance_values: dict[
        tuple[
            str,
            tuple[str, str] | str,
            str,
            tuple[str, str] | str,
            str,
        ],
        float,
    ] = {}
    resistance_unaligned_endpoint = 0
    resistance_unaligned_net = 0
    resistance_duplicates = 0
    for row in resistance_source:
        net_name = mapped_base_net(row["Net"])
        if net_name is None:
            resistance_unaligned_net += 1
            continue
        source = _endpoint(row["DriverPin"], pin_set, io_set)
        target = _endpoint(row["SinkPin"], pin_set, io_set)
        if source is None or target is None:
            resistance_unaligned_endpoint += 1
            continue
        value = _raw_nonnegative(
            row, "EffectiveResistance_ohm", resistance_path
        )
        key = (source[0], source[1], target[0], target[1], net_name)
        previous = resistance_values.get(key)
        if previous is not None:
            resistance_duplicates += 1
            if not _same_value(previous, value):
                raise ValueError(
                    f"{resistance_path} 同一Driver/Sink出现冲突Reff: "
                    f"{key} -> {previous}, {value}"
                )
            continue
        resistance_values[key] = value

    resistance_rows: list[dict[str, Any]] = []
    for key, value in sorted(
        resistance_values.items(),
        key=lambda item: (
            item[0][0],
            str(item[0][1]),
            item[0][2],
            str(item[0][3]),
            net_order[item[0][4]],
        ),
    ):
        source_type, source_key, target_type, target_key, net_name = key
        source_inst, source_pin, source_io = "", "", ""
        target_inst, target_pin, target_io = "", "", ""
        if source_type == "pin":
            source_inst, source_pin = source_key  # type: ignore[misc]
        else:
            source_io = str(source_key)
        if target_type == "pin":
            target_inst, target_pin = target_key  # type: ignore[misc]
        else:
            target_io = str(target_key)
        resistance_rows.append(
            {
                "graph_id": design,
                "net_name": net_name,
                "src_node_type": source_type,
                "src_inst_name": source_inst,
                "src_pin_name": source_pin,
                "src_iopin_name": source_io,
                "dst_node_type": target_type,
                "dst_inst_name": target_inst,
                "dst_pin_name": target_pin,
                "dst_iopin_name": target_io,
                "effective_resistance_ohm": value,
                "valid": 1,
                "label_unit": "ohm",
                "label_transform": "none",
                "label_definition": (
                    "sum of SPEF series resistance on driver-to-sink RC-tree path"
                ),
                "label_source_stage": "post_route_openrcx",
                "label_source_path": str(resistance_path),
                "source_design": (
                    resistance_source[0].get("Design", "")
                    if resistance_source
                    else ""
                ),
            }
        )

    summary = {
        "schema": "r2g2_rc_label_alignment_v1",
        "design": design,
        "source_dir": str(source_dir),
        "raw_physical_values": True,
        "label_transform": "none",
        "source_files": {
            "ground_cap": {
                "path": str(ground_path),
                "sha256": _sha256(ground_path),
            },
            "net_coupling": {
                "path": str(coupling_path),
                "sha256": _sha256(coupling_path),
            },
            "pin_net_resistance": {
                "path": str(resistance_path),
                "sha256": _sha256(resistance_path),
            },
        },
        "ground_cap": {
            "source_rows": len(ground_source),
            "base_nets": len(net_names),
            "aligned_valid_nets": len(ground_values),
            "missing_base_nets": len(net_names) - len(ground_values),
            "source_route_only_nets": ground_extra,
        },
        "coupling_cap": {
            "source_rows": len(coupling_source),
            "aligned_unordered_pairs": len(coupling_rows),
            "source_rows_with_unaligned_net": coupling_unaligned,
            "self_pairs_skipped": coupling_self,
            "pt_storage": "two directed net->net edges per unordered pair",
            "scope": "positive extracted pairs only",
        },
        "effective_resistance": {
            "source_rows": len(resistance_source),
            "aligned_directed_edges": len(resistance_rows),
            "source_rows_with_unaligned_net": resistance_unaligned_net,
            "source_rows_with_unaligned_endpoint": resistance_unaligned_endpoint,
            "duplicate_equal_rows_skipped": resistance_duplicates,
        },
        "stage_boundary": (
            "source labels are post-route; only entities that exist in the "
            "post-synthesis base graph are retained"
        ),
    }
    tables = {
        "ground_cap": ground_rows,
        "net_coupling": coupling_rows,
        "pin_net_resistance": resistance_rows,
    }
    return tables, summary

# ===== 阶段3内置：OpenSTA报告解析与基础图时序实体对齐 =====

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"

START_RE = re.compile(r"^Startpoint:\s+(\S+)", re.MULTILINE)

END_RE = re.compile(r"^Endpoint:\s+(\S+)", re.MULTILINE)

TYPE_RE = re.compile(r"^Path Type:\s+(max|min)\s*$", re.MULTILINE)

SLACK_RE = re.compile(
    rf"^\s*({NUMBER})\s+slack(?:\s+\((?:MET|VIOLATED)\))?\s*$",
    re.MULTILINE,
)

POINT_RE = re.compile(
    rf"^\s*(?P<values>{NUMBER}(?:\s+{NUMBER})*)"
    rf"\s+[\^v]\s+(?P<name>\S+)\s+\([^()]*\)\s*$"
)

TIMING_RELATIONS = (
    ("pin", "timing_path", "pin"),
    ("pin", "timing_path", "io_pin"),
    ("io_pin", "timing_path", "pin"),
    ("io_pin", "timing_path", "io_pin"),
)

TIMING_MANIFEST_SCHEMA = "r2g2-opensta-timing-v3"
TIMING_SOURCE_CONTRACT = "raw_route_def_raw_spef_audited_sdc"

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_timing_manifest(
    *,
    manifest_path: Path,
    sample_id: str,
    max_report: Path,
    min_report: Path,
    expected_inputs: dict[str, Path],
) -> dict[str, Any]:
    """核验OpenSTA报告与当前样本原始输入属于同一份V3来源契约。

    仅检查报告“存在”无法排除旧版Final DEF/SPEF报告被误接入Raw Route
    图。这里同时校验sample_id、契约版本、DEF/SDC/SPEF路径与SHA256，以及
    max/min报告本身的SHA256；任何一项不一致都拒绝生成正式时序标签。
    """

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if payload.get("schema_version") != TIMING_MANIFEST_SCHEMA:
        errors.append(
            "schema_version="
            f"{payload.get('schema_version')!r}, expected={TIMING_MANIFEST_SCHEMA!r}"
        )
    if payload.get("source_contract") != TIMING_SOURCE_CONTRACT:
        errors.append(
            "source_contract="
            f"{payload.get('source_contract')!r}, expected={TIMING_SOURCE_CONTRACT!r}"
        )
    if str(payload.get("sample_id", "")) != sample_id:
        errors.append(
            f"sample_id={payload.get('sample_id')!r}, expected={sample_id!r}"
        )

    manifest_inputs = payload.get("inputs")
    if not isinstance(manifest_inputs, dict):
        errors.append("inputs不是JSON对象")
        manifest_inputs = {}
    for key, expected_path in expected_inputs.items():
        record = manifest_inputs.get(key)
        if not isinstance(record, dict):
            errors.append(f"inputs.{key}缺失")
            continue
        recorded_path = Path(str(record.get("path", ""))).resolve()
        expected_resolved = expected_path.resolve()
        if recorded_path != expected_resolved:
            errors.append(
                f"inputs.{key}.path={recorded_path}, expected={expected_resolved}"
            )
        expected_sha = sha256_file(expected_resolved)
        if str(record.get("sha256", "")) != expected_sha:
            errors.append(
                f"inputs.{key}.sha256不匹配: manifest={record.get('sha256')} "
                f"actual={expected_sha}"
            )

    # 若OpenSTA为兼容特定设计加载了受审计补丁，也必须核验补丁文件本身。
    sdc_patch = manifest_inputs.get("sdc_patch")
    if sdc_patch is not None:
        if not isinstance(sdc_patch, dict):
            errors.append("inputs.sdc_patch不是JSON对象或null")
        else:
            patch_path = Path(str(sdc_patch.get("path", ""))).resolve()
            if not patch_path.is_file():
                errors.append(f"inputs.sdc_patch文件不存在: {patch_path}")
            elif str(sdc_patch.get("sha256", "")) != sha256_file(patch_path):
                errors.append(f"inputs.sdc_patch SHA256不匹配: {patch_path}")

    reports = payload.get("reports")
    if not isinstance(reports, dict):
        errors.append("reports不是JSON对象")
        reports = {}
    for key, actual_path in (
        ("paths_max", max_report),
        ("paths_min", min_report),
    ):
        record = reports.get(key)
        if not isinstance(record, dict):
            errors.append(f"reports.{key}缺失")
            continue
        recorded_path = Path(str(record.get("path", "")))
        if not recorded_path.is_absolute():
            recorded_path = manifest_path.parent / recorded_path
        if recorded_path.resolve() != actual_path.resolve():
            errors.append(
                f"reports.{key}.path={recorded_path.resolve()}, "
                f"expected={actual_path.resolve()}"
            )
        actual_sha = sha256_file(actual_path)
        if str(record.get("sha256", "")) != actual_sha:
            errors.append(
                f"reports.{key}.sha256不匹配: manifest={record.get('sha256')} "
                f"actual={actual_sha}"
            )

    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        errors.append("analysis不是JSON对象")
    else:
        if analysis.get("max_semantics") != "setup":
            errors.append("analysis.max_semantics必须为setup")
        if analysis.get("min_semantics") != "hold":
            errors.append("analysis.min_semantics必须为hold")
        if int(analysis.get("max_path_count", 0)) <= 0:
            errors.append("analysis.max_path_count必须大于0")
        if int(analysis.get("min_path_count", 0)) <= 0:
            errors.append("analysis.min_path_count必须大于0")

    if errors:
        raise ValueError(
            "OpenSTA V3来源契约核验失败，拒绝写入时序标签:\n- "
            + "\n- ".join(errors)
        )
    return {
        "schema": TIMING_MANIFEST_SCHEMA,
        "source_contract": TIMING_SOURCE_CONTRACT,
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "sdc_source": manifest_inputs.get("sdc_source", ""),
        "sdc_patch": sdc_patch,
        "verified_inputs": {
            key: {
                "path": str(path.resolve()),
                "sha256": sha256_file(path.resolve()),
            }
            for key, path in expected_inputs.items()
        },
        "verified_reports": {
            "paths_max": sha256_file(max_report),
            "paths_min": sha256_file(min_report),
        },
    }


@dataclass(frozen=True)
class Point:
    """一条 STA path table 中的时序点和到达该点的增量延时。"""

    name: str
    delay_ns: float

def report_blocks(path: Path) -> Iterator[str]:
    """逐块读取报告，避免一次加载数十 MB 文本。"""

    lines: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("Startpoint:") and lines:
                yield "".join(lines)
                lines = []
            if lines or line.startswith("Startpoint:"):
                lines.append(line)
    if lines:
        yield "".join(lines)

def pin_points_before_arrival(block: str) -> list[Point]:
    """只读取第一处 data arrival time 之前的 launch/data path 表格。"""

    points: list[Point] = []
    for line in block.splitlines():
        if "data arrival time" in line:
            break
        match = POINT_RE.match(line)
        if not match:
            continue
        values = [float(value) for value in match.group("values").split()]
        if len(values) >= 2:
            # 最右列是累计 Time，倒数第二列是从前一时序点到本点的 Delay。
            points.append(
                Point(canonical_name(match.group("name")), values[-2])
            )
    return points

def _is_pin_of(point_name: str, endpoint_or_instance: str) -> bool:
    title = canonical_name(endpoint_or_instance)
    return point_name == title or point_name.startswith(title + "/")

def data_path_points(
    block: str,
) -> tuple[str, float, list[Point]] | None:
    """返回 ``(max|min, slack_ns, launch到endpoint的数据时序点)``。"""

    start_match = START_RE.search(block)
    end_match = END_RE.search(block)
    type_match = TYPE_RE.search(block)
    slack_match = SLACK_RE.search(block)
    if not all((start_match, end_match, type_match, slack_match)):
        return None

    points = pin_points_before_arrival(block)
    end_candidates = [
        index
        for index, point in enumerate(points)
        if _is_pin_of(point.name, end_match.group(1))
    ]
    if not end_candidates:
        return None
    end_index = end_candidates[-1]
    start_candidates = [
        index
        for index, point in enumerate(points[: end_index + 1])
        if _is_pin_of(point.name, start_match.group(1))
    ]
    if not start_candidates:
        return None
    # 对时序单元而言，最后一个同实例点通常是 Q/QN，前面的 CK 不属于数据路径。
    start_index = start_candidates[-1]
    if start_index > end_index:
        return None
    return (
        type_match.group(1),
        float(slack_match.group(1)),
        points[start_index : end_index + 1],
    )

def _worst_slack(current: float | None, value: float) -> float:
    """Setup/Hold 都以较小 slack 为更坏值。"""

    return value if current is None else min(current, value)

def parse_reports(
    reports: Iterable[tuple[Path, str]],
) -> tuple[
    dict[str, dict[str, float | None]],
    dict[tuple[str, str], dict[str, float | None]],
    dict[str, Any],
    list[tuple[str, list[Point]]],
]:
    """解析 max/min 报告并构造有向时序点与边。

    同一条有向边若被多条路径观察：

    * max/setup 报告保留最大的增量延时；
    * min/hold 报告保留最小的增量延时。
    """

    nodes: dict[str, dict[str, float | None]] = {}
    edges: dict[tuple[str, str], dict[str, float | None]] = {}
    stats: dict[str, Any] = {
        "blocks": 0,
        "parsed_paths": 0,
        "skipped_blocks": 0,
        "type_mismatches": 0,
        "paths_by_type": {"max": 0, "min": 0},
    }
    source_reports: list[dict[str, Any]] = []
    parsed_path_points: list[tuple[str, list[Point]]] = []

    for report, expected_type in reports:
        report_stats = {"blocks": 0, "parsed_paths": 0, "skipped_blocks": 0}
        for block in report_blocks(report):
            stats["blocks"] += 1
            report_stats["blocks"] += 1
            parsed = data_path_points(block)
            if parsed is None:
                stats["skipped_blocks"] += 1
                report_stats["skipped_blocks"] += 1
                continue
            path_type, slack_ns, points = parsed
            if path_type != expected_type:
                stats["type_mismatches"] += 1
                continue
            stats["parsed_paths"] += 1
            stats["paths_by_type"][path_type] += 1
            report_stats["parsed_paths"] += 1
            parsed_path_points.append((path_type, points))

            for point in points:
                nodes.setdefault(
                    point.name,
                    {"setup_slack_ns": None, "hold_slack_ns": None},
                )
            slack_field = (
                "setup_slack_ns" if path_type == "max" else "hold_slack_ns"
            )
            endpoint = nodes[points[-1].name]
            endpoint[slack_field] = _worst_slack(
                endpoint[slack_field], slack_ns
            )

            delay_field = (
                "setup_delay_ns" if path_type == "max" else "hold_delay_ns"
            )
            for source, target in zip(points, points[1:]):
                key = (source.name, target.name)
                edge = edges.setdefault(
                    key,
                    {"setup_delay_ns": None, "hold_delay_ns": None},
                )
                current = edge[delay_field]
                if current is None:
                    edge[delay_field] = target.delay_ns
                elif path_type == "max":
                    edge[delay_field] = max(current, target.delay_ns)
                else:
                    edge[delay_field] = min(current, target.delay_ns)

        source_reports.append(
            {
                "path": str(report),
                "path_type": expected_type,
                "sha256": sha256_file(report),
                **report_stats,
            }
        )
    stats["source_reports"] = source_reports
    stats["timing_point_count"] = len(nodes)
    stats["directed_edge_count"] = len(edges)
    stats["parse_success_ratio"] = (
        stats["parsed_paths"] / stats["blocks"] if stats["blocks"] else 0.0
    )
    missing_types = [
        path_type
        for path_type, count in stats["paths_by_type"].items()
        if count == 0
    ]
    if stats["blocks"] == 0 or stats["parsed_paths"] == 0 or missing_types:
        raise ValueError(
            "OpenSTA时序报告没有形成完整的max/min解析结果: "
            f"blocks={stats['blocks']}, "
            f"parsed_paths={stats['parsed_paths']}, "
            f"skipped_blocks={stats['skipped_blocks']}, "
            f"type_mismatches={stats['type_mismatches']}, "
            f"paths_by_type={stats['paths_by_type']}, "
            f"missing_types={missing_types}"
        )
    return nodes, edges, stats, parsed_path_points

def classify_point(
    name: str,
    pin_keys: set[tuple[str, str]],
    io_keys: set[str],
) -> tuple[str, Any] | None:
    """把 STA 名称映射为基础图内部 Pin 或顶层 IO Pin 稳定键。"""

    clean = canonical_name(name)
    if "/" in clean:
        inst_name, pin_name = clean.rsplit("/", 1)
        key = (canonical_name(inst_name), canonical_name(pin_name))
        return ("pin", key) if key in pin_keys else None
    return ("io_pin", clean) if clean in io_keys else None

def build_aligned_tables(
    *,
    design: str,
    pin_keys: list[tuple[str, str]],
    io_keys: list[str],
    max_report: Path,
    min_report: Path,
    physical_instances: set[str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """生成与基础图完整节点集合对齐的标签表，以及对齐后的任务边表。"""

    node_values, raw_edge_values, parse_stats, parsed_paths = parse_reports(
        [(max_report, "max"), (min_report, "min")]
    )
    pin_set = set(pin_keys)
    io_set = set(io_keys)
    base_instances = {inst_name for inst_name, _ in pin_keys}
    physical_instance_set = {
        canonical_name(name) for name in (physical_instances or set())
    }
    mapped_nodes: dict[str, tuple[str, Any]] = {}
    passthrough_nodes: set[str] = set()
    rejected_points: list[str] = []
    for name in node_values:
        mapped = classify_point(name, pin_set, io_set)
        if mapped is not None:
            mapped_nodes[name] = mapped
            continue
        if "/" in name:
            inst_name = canonical_name(name.rsplit("/", 1)[0])
            # 只允许“基础图中不存在、但同版本物理DEF中存在”的后端插入单元。
            if (
                inst_name not in base_instances
                and inst_name in physical_instance_set
            ):
                passthrough_nodes.add(name)
                continue
        rejected_points.append(name)

    # 对合法后端插入单元执行有向路径收缩。收缩边的delay等于从上一个基础时序点
    # 到下一个基础时序点之间所有增量delay之和。
    edge_values: dict[tuple[str, str], dict[str, float | None]] = {}
    fully_consistent_paths = 0
    paths_with_contracted_points = 0
    for path_type, points in parsed_paths:
        last_mapped_index: int | None = None
        last_mapped_name = ""
        rejected_since_last_mapped = False
        path_consistent = True
        path_has_passthrough = False
        for index, point in enumerate(points):
            if point.name in passthrough_nodes:
                path_has_passthrough = True
                continue
            if point.name not in mapped_nodes:
                rejected_since_last_mapped = True
                path_consistent = False
                continue
            if (
                last_mapped_index is not None
                and not rejected_since_last_mapped
            ):
                delay_ns = sum(
                    item.delay_ns
                    for item in points[last_mapped_index + 1 : index + 1]
                )
                key = (last_mapped_name, point.name)
                values = edge_values.setdefault(
                    key,
                    {"setup_delay_ns": None, "hold_delay_ns": None},
                )
                field = (
                    "setup_delay_ns"
                    if path_type == "max"
                    else "hold_delay_ns"
                )
                current = values[field]
                if current is None:
                    values[field] = delay_ns
                elif path_type == "max":
                    values[field] = max(current, delay_ns)
                else:
                    values[field] = min(current, delay_ns)
            last_mapped_index = index
            last_mapped_name = point.name
            rejected_since_last_mapped = False
        if path_consistent:
            fully_consistent_paths += 1
            if path_has_passthrough:
                paths_with_contracted_points += 1

    pin_labels: dict[tuple[str, str], dict[str, float | None]] = {}
    io_labels: dict[str, dict[str, float | None]] = {}
    for name, mapped in mapped_nodes.items():
        node_type, key = mapped
        if node_type == "pin":
            pin_labels[key] = node_values[name]
        else:
            io_labels[key] = node_values[name]

    common_source = f"{max_report};{min_report}"
    pin_rows: list[dict[str, Any]] = []
    for inst_name, pin_name in pin_keys:
        values = pin_labels.get((inst_name, pin_name), {})
        setup = values.get("setup_slack_ns")
        hold = values.get("hold_slack_ns")
        pin_rows.append(
            {
                "graph_id": design,
                "inst_name": inst_name,
                "pin_name": pin_name,
                "setup_slack_ns": setup if setup is not None else float("nan"),
                "hold_slack_ns": hold if hold is not None else float("nan"),
                "setup_valid": int(setup is not None and math.isfinite(setup)),
                "hold_valid": int(hold is not None and math.isfinite(hold)),
                "is_timing_endpoint": int(setup is not None or hold is not None),
                "label_unit": "ns",
                "label_transform": "none",
                "label_source_stage": "post-route/opensta",
                "label_source_path": common_source,
            }
        )

    io_rows: list[dict[str, Any]] = []
    for io_name in io_keys:
        values = io_labels.get(io_name, {})
        setup = values.get("setup_slack_ns")
        hold = values.get("hold_slack_ns")
        io_rows.append(
            {
                "graph_id": design,
                "iopin_name": io_name,
                "setup_slack_ns": setup if setup is not None else float("nan"),
                "hold_slack_ns": hold if hold is not None else float("nan"),
                "setup_valid": int(setup is not None and math.isfinite(setup)),
                "hold_valid": int(hold is not None and math.isfinite(hold)),
                "is_timing_endpoint": int(setup is not None or hold is not None),
                "label_unit": "ns",
                "label_transform": "none",
                "label_source_stage": "post-route/opensta",
                "label_source_path": common_source,
            }
        )

    relation_rows: dict[str, list[dict[str, Any]]] = {
        "|".join(relation): [] for relation in TIMING_RELATIONS
    }
    edge_category_counts: Counter[str] = Counter()
    missing_edge_examples: list[dict[str, str]] = []
    for (source_name, target_name), values in sorted(edge_values.items()):
        source = mapped_nodes.get(source_name)
        target = mapped_nodes.get(target_name)
        if source is None or target is None:
            edge_category_counts["unmapped"] += 1
            if len(missing_edge_examples) < 20:
                missing_edge_examples.append(
                    {"source": source_name, "target": target_name}
                )
            continue
        source_type, source_key = source
        target_type, target_key = target
        relation_key = f"{source_type}|timing_path|{target_type}"
        edge_category_counts[relation_key] += 1
        setup = values["setup_delay_ns"]
        hold = values["hold_delay_ns"]
        row: dict[str, Any] = {
            "graph_id": design,
            "setup_delay_ns": (
                setup if setup is not None else float("nan")
            ),
            "hold_delay_ns": hold if hold is not None else float("nan"),
            "setup_valid": int(setup is not None and math.isfinite(setup)),
            "hold_valid": int(hold is not None and math.isfinite(hold)),
            "label_unit": "ns",
            "label_transform": "none",
            "label_source_stage": "post-route/opensta",
            "label_source_path": common_source,
        }
        if source_type == "pin":
            row["src_inst_name"], row["src_pin_name"] = source_key
        else:
            row["src_iopin_name"] = source_key
        if target_type == "pin":
            row["dst_inst_name"], row["dst_pin_name"] = target_key
        else:
            row["dst_iopin_name"] = target_key
        relation_rows[relation_key].append(row)

    setup_pin_count = sum(row["setup_valid"] for row in pin_rows)
    hold_pin_count = sum(row["hold_valid"] for row in pin_rows)
    setup_io_count = sum(row["setup_valid"] for row in io_rows)
    hold_io_count = sum(row["hold_valid"] for row in io_rows)
    mapped_count = len(mapped_nodes)
    aligned_edge_count = sum(len(rows) for rows in relation_rows.values())
    point_count = len(node_values)
    raw_edge_count = len(raw_edge_values)
    consistent_point_count = len(mapped_nodes) + len(passthrough_nodes)
    parsed_path_count = len(parsed_paths)
    summary = {
        "schema": "r2g2_opensta_timing_alignment_v1",
        "design": design,
        "directed": True,
        "delay_semantics": {
            "setup_delay_ns": "max incremental delay observed in max paths",
            "hold_delay_ns": "min incremental delay observed in min paths",
        },
        "slack_semantics": {
            "setup_slack_ns": "minimum endpoint slack observed in max paths",
            "hold_slack_ns": "minimum endpoint slack observed in min paths",
        },
        "parse": parse_stats,
        "alignment": {
            "report_timing_points": point_count,
            "mapped_timing_points": mapped_count,
            "verified_inserted_timing_points": len(passthrough_nodes),
            "rejected_timing_points": len(rejected_points),
            "point_alignment_ratio": mapped_count / point_count if point_count else 0.0,
            "source_consistency_ratio": (
                consistent_point_count / point_count if point_count else 0.0
            ),
            "parsed_paths": parsed_path_count,
            "fully_source_consistent_paths": fully_consistent_paths,
            "paths_with_contracted_inserted_cells": paths_with_contracted_points,
            "path_source_consistency_ratio": (
                fully_consistent_paths / parsed_path_count
                if parsed_path_count
                else 0.0
            ),
            "report_directed_edges": raw_edge_count,
            "mapped_directed_edges": aligned_edge_count,
            "contracted_or_directed_edges": len(edge_values),
            "edge_alignment_ratio": (
                aligned_edge_count / raw_edge_count
                if raw_edge_count
                else 0.0
            ),
            "relation_edge_counts": dict(edge_category_counts),
            "verified_inserted_point_examples": sorted(passthrough_nodes)[:20],
            "rejected_point_examples": rejected_points[:20],
            "missing_edge_examples": missing_edge_examples,
        },
        "finite_endpoint_labels": {
            "pin_setup": setup_pin_count,
            "pin_hold": hold_pin_count,
            "io_pin_setup": setup_io_count,
            "io_pin_hold": hold_io_count,
        },
    }
    return {
        "pin_timing": pin_rows,
        "iopin_timing": io_rows,
        **relation_rows,
    }, summary

def canonical_name(value: str) -> str:
    r"""统一 Verilog/DEF转义及层级分隔符形式。

    Yosys常写``\foo[0]``并在flatten后用``.``连接层次，OpenROAD DEF常写
    ``foo\[0\]``并用``/``连接层次。基础图统一保存Yosys风格的``.``，使
    ``a.b.c``与``a.b/c``得到同一个稳定实体键。
    """

    return (value or "").replace("\\", "").replace("/", ".").strip()


def load_config(path: str) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        return config_path, json.load(handle)


def resolve_path(
    config_path: Path, value: str, required: bool = True
) -> Path | None:
    if not value:
        if required:
            raise ValueError("配置缺少必要路径")
        return None
    path = Path(value)
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    if required and not path.is_file():
        raise FileNotFoundError(path)
    return path


def output_dir_from_config(config_path: Path, cfg: dict[str, Any]) -> Path:
    design = str(cfg.get("design_name") or config_path.stem)
    raw = str(cfg.get("output_dir", f"output/{design}"))
    path = Path(raw)
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def validate_label_stage(
    config_path: Path, cfg: dict[str, Any], label_def: Path
) -> str:
    """动态数据集使用manifest强制确认标签DEF属于routing阶段。"""

    raw = str(cfg.get("raw_manifest", ""))
    if not raw:
        return "route/post-route"
    manifest_path = resolve_path(config_path, raw, True)
    assert manifest_path is not None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = manifest.get("artifacts", {}).get("routing_def")
    if not artifact:
        raise ValueError("manifest 缺少 routing_def artifact")
    expected_path = (manifest_path.parent / artifact["path"]).resolve()
    if label_def != expected_path:
        raise ValueError(
            f"标签DEF必须是manifest routing_def: "
            f"actual={label_def}, manifest={expected_path}"
        )
    semantics = str(artifact.get("semantics", "")).lower()
    if "routing" not in semantics:
        raise ValueError(f"routing_def semantics异常: {semantics!r}")
    return semantics


def iter_def_entries(path: Path, section: str):
    active = False
    buffer: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith(section) and not line.startswith(f"END {section}"):
                active = True
                continue
            if active and line.startswith(f"END {section}"):
                if buffer:
                    yield " ".join(buffer)
                break
            if not active:
                continue
            if line.startswith("-"):
                if buffer:
                    yield " ".join(buffer)
                buffer = [line]
            elif buffer:
                buffer.append(line)
            if buffer and ";" in line:
                yield " ".join(buffer)
                buffer = []


def parse_def_header(
    path: Path, grid_um: float = FIXED_CONGESTION_GRID_UM
) -> dict[str, Any]:
    """读取DEF头部并给出固定拥塞网格步长。

    r2g-skills delta vs upstream R2G2.0: ``grid_um``从写死的2.1um(Nangate45
    的15×Metal3 pitch)改为可传入。标签网格必须与02的特征网格逐位相同, 否则
    04会以"拥塞特征与标签的GCell规格不一致"硬失败。
    """

    dbu = 1.0
    def_gcell_x = def_gcell_y = 0
    die = (0, 0, 0, 0)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            unit = re.match(r"UNITS DISTANCE MICRONS\s+(\d+)", line)
            if unit:
                dbu = float(unit.group(1))
            if line.startswith("GCELLGRID X"):
                match = re.search(r"\bSTEP\s+(\d+)", line)
                if match:
                    def_gcell_x = int(match.group(1))
            if line.startswith("GCELLGRID Y"):
                match = re.search(r"\bSTEP\s+(\d+)", line)
                if match:
                    def_gcell_y = int(match.group(1))
            if line.startswith("DIEAREA"):
                numbers = [int(value) for value in re.findall(r"-?\d+", line)]
                if len(numbers) >= 4:
                    die = (numbers[0], numbers[1], numbers[-2], numbers[-1])
            if line.startswith("COMPONENTS"):
                break
    grid_step_dbu = int(round(float(grid_um) * dbu))
    return {
        "dbu": dbu,
        "gcell_x": grid_step_dbu,
        "gcell_y": grid_step_dbu,
        "die": die,
        "def_gcell_x": def_gcell_x,
        "def_gcell_y": def_gcell_y,
        "grid_spec_source": (
            "technology_constant_15x_metal3_pitch_pre_route"
        ),
    }


def route_clauses(entry: str):
    """从一条Net记录中切出每个ROUTED/NEW/FIXED布线子句。"""

    starts = list(re.finditer(r"\b(ROUTED|NEW|FIXED)\s+(\S+)", entry))
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(entry)
        yield canonical_name(match.group(2)), entry[match.end() : end]


def clause_points(text: str) -> list[tuple[str, str]]:
    return re.findall(
        r"\(\s*([*]|-?\d+)\s+([*]|-?\d+)(?:\s+[^\)]*)?\)", text
    )


def segments_from_clause(text: str):
    points = clause_points(text)
    if len(points) < 2:
        return
    current_x: int | None = None
    current_y: int | None = None
    for x_token, y_token in points:
        if current_x is None or current_y is None:
            if x_token == "*" or y_token == "*":
                continue
            current_x, current_y = int(x_token), int(y_token)
            continue
        next_x = current_x if x_token == "*" else int(x_token)
        next_y = current_y if y_token == "*" else int(y_token)
        if next_x != current_x or next_y != current_y:
            yield current_x, current_y, next_x, next_y
        current_x, current_y = next_x, next_y


def extract_wirelengths(path: Path) -> tuple[dict[str, float], dict[str, str]]:
    header = parse_def_header(path)
    lengths: dict[str, float] = {}
    uses: dict[str, str] = {}
    for entry in iter_def_entries(path, "NETS"):
        name_match = re.match(r"-\s+(\S+)", entry)
        if not name_match:
            continue
        name = canonical_name(name_match.group(1))
        use_match = re.search(r"\+\s*USE\s+(\S+)", entry)
        uses[name] = canonical_name(use_match.group(1)).upper() if use_match else "SIGNAL"
        total_dbu = 0
        for _, clause in route_clauses(entry):
            for x1, y1, x2, y2 in segments_from_clause(clause):
                total_dbu += abs(x2 - x1) + abs(y2 - y1)
        lengths[name] = total_dbu / header["dbu"]
    return lengths, uses


def parse_tech_lef(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """读取所有 TYPE ROUTING 层的 pitch 和首选方向，不使用工艺硬编码。"""

    layers: dict[str, dict[str, Any]] = {}
    for path in paths:
        current = ""
        block: dict[str, Any] = {}
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                tokens = raw.replace(";", " ").split()
                if not tokens:
                    continue
                if tokens[0] == "LAYER" and len(tokens) >= 2:
                    current = canonical_name(tokens[1])
                    block = {"type": "", "pitch": [], "direction": ""}
                    continue
                if not current:
                    continue
                if tokens[0] == "TYPE" and len(tokens) >= 2:
                    block["type"] = tokens[1].upper()
                elif tokens[0] == "PITCH":
                    for token in tokens[1:]:
                        try:
                            block["pitch"].append(float(token))
                        except ValueError:
                            pass
                elif tokens[0] == "DIRECTION" and len(tokens) >= 2:
                    block["direction"] = tokens[1].upper()
                elif tokens[0] == "END":
                    if (
                        block.get("type") == "ROUTING"
                        and block.get("pitch")
                        and block.get("direction")
                    ):
                        pitch_values = block["pitch"]
                        direction = block["direction"]
                        pitch = (
                            pitch_values[1]
                            if len(pitch_values) > 1 and direction == "HORIZONTAL"
                            else pitch_values[0]
                        )
                        layers[current] = {
                            "pitch_um": pitch,
                            "direction": direction,
                        }
                    current = ""
                    block = {}
    if not layers:
        raise ValueError("LEF中没有解析到TYPE ROUTING层，不能计算拥塞容量")
    return layers


def add_segment_demand(
    demand: dict[tuple[int, int], float],
    fixed: int,
    start: int,
    end: int,
    main_step: int,
    fixed_step: int,
    dbu: float,
    vertical: bool,
) -> None:
    low, high = sorted((start, end))
    cursor = low
    fixed_index = fixed // fixed_step
    while cursor < high:
        main_index = cursor // main_step
        next_boundary = (main_index + 1) * main_step
        nxt = min(high, next_boundary)
        key = (
            (fixed_index, main_index) if vertical else (main_index, fixed_index)
        )
        demand[key] += (nxt - cursor) / dbu
        cursor = nxt


def extract_grid_utilization(
    path: Path,
    header: dict[str, Any],
    layers: dict[str, dict[str, Any]],
) -> dict[tuple[int, int], float]:
    step_x = header["gcell_x"]
    step_y = header["gcell_y"]
    if step_x <= 0 or step_y <= 0:
        raise ValueError("由15×Metal3 pitch得到的固定拥塞网格无效")
    demand_h: dict[tuple[int, int], float] = defaultdict(float)
    demand_v: dict[tuple[int, int], float] = defaultdict(float)
    for entry in iter_def_entries(path, "NETS"):
        for _, clause in route_clauses(entry):
            for x1, y1, x2, y2 in segments_from_clause(clause):
                if x1 != x2:
                    add_segment_demand(
                        demand_h,
                        y1 - header["die"][1],
                        x1 - header["die"][0],
                        x2 - header["die"][0],
                        step_x,
                        step_y,
                        header["dbu"],
                        False,
                    )
                if y1 != y2:
                    add_segment_demand(
                        demand_v,
                        x2 - header["die"][0],
                        y1 - header["die"][1],
                        y2 - header["die"][1],
                        step_y,
                        step_x,
                        header["dbu"],
                        True,
                    )

    grid_width_um = step_x / header["dbu"]
    grid_height_um = step_y / header["dbu"]
    capacity_h = sum(
        grid_width_um * (grid_height_um / info["pitch_um"])
        for info in layers.values()
        if info["direction"] == "HORIZONTAL" and info["pitch_um"] > 0
    )
    capacity_v = sum(
        grid_height_um * (grid_width_um / info["pitch_um"])
        for info in layers.values()
        if info["direction"] == "VERTICAL" and info["pitch_um"] > 0
    )
    if capacity_h <= 0 or capacity_v <= 0:
        raise ValueError("由LEF计算出的水平/垂直routing capacity无效")
    return {
        key: max(demand_h.get(key, 0.0) / capacity_h, demand_v.get(key, 0.0) / capacity_v)
        for key in set(demand_h) | set(demand_v)
    }


def parse_components(path: Path) -> dict[str, dict[str, Any]]:
    components: dict[str, dict[str, Any]] = {}
    for entry in iter_def_entries(path, "COMPONENTS"):
        parts = entry.split()
        if len(parts) < 3:
            continue
        place = re.search(
            r"\+\s*(?:PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", entry
        )
        components[canonical_name(parts[1])] = {
            "master": canonical_name(parts[2]),
            "x": int(place.group(1)) if place else None,
            "y": int(place.group(2)) if place else None,
        }
    return components


def congestion_at(
    utilization: dict[tuple[int, int], float],
    grid_x: int,
    grid_y: int,
    radius: int,
) -> float:
    if radius <= 0:
        return utilization.get((grid_x, grid_y), 0.0)
    weighted = weight_sum = 0.0
    sigma = max(1.0, radius / 2.0)
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            weight = math.exp(-(dx * dx + dy * dy) / (2.0 * sigma * sigma))
            weighted += weight * utilization.get((grid_x + dx, grid_y + dy), 0.0)
            weight_sum += weight
    return weighted / weight_sum if weight_sum else 0.0


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    _write_csv(path, fieldnames, rows)
    print(f"[label] {path.name}: {len(rows)} rows")


GROUND_NAMES = {"0", "gnd", "vss"}
SPICE_SUFFIXES = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}


def is_ground(node: str) -> bool:
    return node.strip().lower() in GROUND_NAMES


def parse_spice_number(value: str) -> float:
    """解析SPICE数值，支持科学计数法和常见工程后缀。"""

    token = value.strip().rstrip(",")
    if "=" in token:
        token = token.split("=", 1)[1]
    try:
        return float(token)
    except ValueError:
        match = re.fullmatch(
            r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
            r"([A-Za-z]+)",
            token,
        )
        if not match:
            raise ValueError(f"无法解析SPICE数值: {value!r}")
        suffix = match.group(2).lower()
        if suffix not in SPICE_SUFFIXES:
            raise ValueError(f"不支持的SPICE数值后缀: {value!r}")
        return float(match.group(1)) * SPICE_SUFFIXES[suffix]


def _source_value(tokens: list[str]) -> float:
    for index, token in enumerate(tokens):
        if token.upper() == "DC" and index + 1 < len(tokens):
            return parse_spice_number(tokens[index + 1])
    return parse_spice_number(tokens[-1])


def parse_vdd_spice(path: Path) -> dict[str, Any]:
    """解析PDNSim VDD SPICE中的R、I和固定电压源。

    ``* Sink for <instance>/<pin>`` 注释是SP节点到逻辑Gate实例的稳定映射。
    电流源即使缺少该注释也会参加电网求解，但不会生成Gate标签。
    """

    resistors: list[tuple[str, str, float]] = []
    currents: list[dict[str, Any]] = []
    fixed_voltages: dict[str, float] = {}
    pending_sink: tuple[str, str] | None = None
    malformed: list[tuple[int, str]] = []
    sink_pattern = re.compile(
        r"^\*\s*Sink\s+for\s+(.+?)/([^/\s]+)\s*$", re.IGNORECASE
    )

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            sink_match = sink_pattern.match(line)
            if sink_match:
                pending_sink = (
                    canonical_name(sink_match.group(1)),
                    canonical_name(sink_match.group(2)),
                )
                continue
            if line.startswith("*") or line.startswith("."):
                continue
            tokens = line.split()
            if len(tokens) < 4:
                malformed.append((line_number, line))
                continue
            element = tokens[0][0].upper()
            try:
                if element == "R":
                    resistance = parse_spice_number(tokens[-1])
                    if not math.isfinite(resistance) or resistance <= 0:
                        raise ValueError("电阻必须为有限正数")
                    resistors.append((tokens[1], tokens[2], resistance))
                elif element == "I":
                    instance, pin = pending_sink or ("", "")
                    currents.append(
                        {
                            "source_name": tokens[0],
                            "node_positive": tokens[1],
                            "node_negative": tokens[2],
                            "current_A": _source_value(tokens[3:]),
                            "inst_name": instance,
                            "pin_name": pin,
                        }
                    )
                    pending_sink = None
                elif element == "V":
                    voltage = _source_value(tokens[3:])
                    positive, negative = tokens[1], tokens[2]
                    if is_ground(negative) and not is_ground(positive):
                        node, node_voltage = positive, voltage
                    elif is_ground(positive) and not is_ground(negative):
                        node, node_voltage = negative, -voltage
                    else:
                        raise ValueError(
                            "当前VDD求解器只支持一端接地的独立电压源"
                        )
                    previous = fixed_voltages.get(node)
                    if previous is not None and not math.isclose(
                        previous, node_voltage, rel_tol=1e-9, abs_tol=1e-12
                    ):
                        raise ValueError(
                            f"节点{node}存在冲突电压源: {previous}, {node_voltage}"
                        )
                    fixed_voltages[node] = node_voltage
            except ValueError as error:
                raise ValueError(
                    f"{path}:{line_number}: {error}; line={line!r}"
                ) from error

    if not resistors:
        raise ValueError(f"{path}没有解析到电阻网络")
    if not currents:
        raise ValueError(f"{path}没有解析到电流负载")
    if not fixed_voltages:
        raise ValueError(f"{path}没有解析到VDD固定电压源")
    if malformed:
        examples = malformed[:3]
        raise ValueError(f"{path}存在{len(malformed)}条格式不完整记录，例如{examples}")

    return {
        "resistors": resistors,
        "currents": currents,
        "fixed_voltages": fixed_voltages,
    }


def _assert_all_unknown_components_are_powered(
    nodes: set[str],
    resistors: list[tuple[str, str, float]],
    fixed_voltages: dict[str, float],
) -> None:
    """用并查集确认每个待求节点都通过电阻网络连接到VDD源。"""

    parent = {node: node for node in nodes}

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != node:
            nxt = parent[node]
            parent[node] = root
            node = nxt
        return root

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right, _ in resistors:
        if not is_ground(left) and not is_ground(right):
            union(left, right)
    powered_roots = {find(node) for node in fixed_voltages}
    unpowered = [
        node
        for node in nodes
        if node not in fixed_voltages and find(node) not in powered_roots
    ]
    if unpowered:
        raise ValueError(
            f"VDD电阻网络存在未连接电源的节点: count={len(unpowered)}, "
            f"examples={unpowered[:5]}"
        )


def solve_vdd_network(
    network: dict[str, Any],
    supply_voltage: float | None = None,
    relative_tolerance: float = 1e-10,
    max_iterations: int = 20000,
    solver: str = "direct",
) -> tuple[dict[str, float], dict[str, Any]]:
    """使用稀疏节点分析求解VDD电网直流工作点。

    对每个未知节点建立KCL：

    ``sum_j((V_i - V_j) / R_ij) = -I_sink_i``。

    固定电压源作为Dirichlet边界。默认使用SuperLU稀疏直接求解；也可选择
    Jacobi预条件共轭梯度。两者都不会把十万级稀疏电网转成稠密矩阵。
    """

    if relative_tolerance <= 0 or max_iterations <= 0:
        raise ValueError("IRDrop求解器rtol和max_iterations必须为正数")
    if solver not in {"direct", "cg"}:
        raise ValueError("IRDrop solver必须是direct或cg")
    _sparse = _require_scipy()
    coo_matrix = _sparse["coo_matrix"]
    LinearOperator = _sparse["LinearOperator"]
    MatrixRankWarning = _sparse["MatrixRankWarning"]
    cg = _sparse["cg"]
    spsolve = _sparse["spsolve"]
    resistors = network["resistors"]
    currents = network["currents"]
    fixed_voltages = network["fixed_voltages"]
    source_values = np.asarray(list(fixed_voltages.values()), dtype=np.float64)
    inferred_supply = float(source_values.max())
    if supply_voltage is None:
        supply_voltage = inferred_supply
    if not math.isfinite(supply_voltage) or supply_voltage <= 0:
        raise ValueError(f"无效VDD供电电压: {supply_voltage}")
    if not np.allclose(
        source_values, supply_voltage, rtol=1e-7, atol=1e-9
    ):
        raise ValueError(
            "VDD_extracted.sp中的电压源与配置供电电压不一致: "
            f"sources=[{source_values.min()}, {source_values.max()}], "
            f"configured={supply_voltage}"
        )

    nodes: set[str] = set(fixed_voltages)
    for left, right, _ in resistors:
        if not is_ground(left):
            nodes.add(left)
        if not is_ground(right):
            nodes.add(right)
    for current in currents:
        for node in (current["node_positive"], current["node_negative"]):
            if not is_ground(node):
                nodes.add(node)
    _assert_all_unknown_components_are_powered(nodes, resistors, fixed_voltages)

    unknown_nodes = sorted(nodes - set(fixed_voltages))
    unknown_index = {node: index for index, node in enumerate(unknown_nodes)}
    rhs = np.zeros(len(unknown_nodes), dtype=np.float64)
    rows: list[int] = []
    columns: list[int] = []
    values: list[float] = []

    def fixed_voltage(node: str) -> float:
        return 0.0 if is_ground(node) else fixed_voltages[node]

    for left, right, resistance in resistors:
        conductance = 1.0 / resistance
        for node, other in ((left, right), (right, left)):
            if is_ground(node) or node in fixed_voltages:
                continue
            index = unknown_index[node]
            rows.append(index)
            columns.append(index)
            values.append(conductance)
            if not is_ground(other) and other not in fixed_voltages:
                rows.append(index)
                columns.append(unknown_index[other])
                values.append(-conductance)
            else:
                rhs[index] += conductance * fixed_voltage(other)

    total_sink_current = 0.0
    for current in currents:
        value = float(current["current_A"])
        if not math.isfinite(value):
            raise ValueError(f"电流源包含非有限值: {current}")
        positive = current["node_positive"]
        negative = current["node_negative"]
        if not is_ground(positive) and positive not in fixed_voltages:
            rhs[unknown_index[positive]] -= value
        if not is_ground(negative) and negative not in fixed_voltages:
            rhs[unknown_index[negative]] += value
        if is_ground(negative) and value > 0:
            total_sink_current += value

    matrix = coo_matrix(
        (np.asarray(values), (np.asarray(rows), np.asarray(columns))),
        shape=(len(unknown_nodes), len(unknown_nodes)),
        dtype=np.float64,
    ).tocsr()
    diagonal = matrix.diagonal()
    if np.any(diagonal <= 0) or not np.isfinite(diagonal).all():
        raise ValueError("VDD导纳矩阵存在非正或非有限对角项")
    iteration_count: int | None = None
    if solver == "direct":
        with warnings.catch_warnings():
            warnings.simplefilter("error", MatrixRankWarning)
            solution = spsolve(matrix, rhs)
    else:
        preconditioner = LinearOperator(
            matrix.shape,
            matvec=lambda vector: vector / diagonal,
            dtype=np.float64,
        )
        iteration_count = 0

        def count_iteration(_: np.ndarray) -> None:
            nonlocal iteration_count
            assert iteration_count is not None
            iteration_count += 1

        solution, info = cg(
            matrix,
            rhs,
            rtol=relative_tolerance,
            atol=0.0,
            maxiter=max_iterations,
            M=preconditioner,
            callback=count_iteration,
        )
        if info != 0:
            reason = (
                f"达到max_iterations={info}" if info > 0 else "输入矩阵/求解器异常"
            )
            raise RuntimeError(f"VDD稀疏CG求解未收敛: {reason}")
    if not np.isfinite(solution).all():
        raise RuntimeError("VDD稀疏求解得到NaN/Inf")
    residual = matrix @ solution - rhs
    residual_l2 = float(np.linalg.norm(residual))
    relative_residual = residual_l2 / max(float(np.linalg.norm(rhs)), 1e-30)
    node_voltage = dict(fixed_voltages)
    node_voltage.update(
        {node: float(solution[index]) for node, index in unknown_index.items()}
    )
    delivered_supply_current = 0.0
    for left, right, resistance in resistors:
        left_voltage = 0.0 if is_ground(left) else node_voltage[left]
        right_voltage = 0.0 if is_ground(right) else node_voltage[right]
        if left in fixed_voltages:
            delivered_supply_current += (left_voltage - right_voltage) / resistance
        if right in fixed_voltages:
            delivered_supply_current += (right_voltage - left_voltage) / resistance
    current_balance_error = delivered_supply_current - total_sink_current
    statistics = {
        "supply_voltage_V": supply_voltage,
        "resistor_count": len(resistors),
        "current_source_count": len(currents),
        "voltage_source_count": len(fixed_voltages),
        "node_count": len(nodes),
        "unknown_node_count": len(unknown_nodes),
        "matrix_nonzeros": int(matrix.nnz),
        "solver": "superlu_sparse_direct" if solver == "direct" else "jacobi_cg",
        "solver_iterations": iteration_count,
        "solver_relative_tolerance": relative_tolerance,
        "solver_relative_residual": relative_residual,
        "solver_residual_l2": residual_l2,
        "total_sink_current_A": total_sink_current,
        "delivered_supply_current_A": delivered_supply_current,
        "current_balance_error_A": current_balance_error,
        "current_balance_relative_error": (
            current_balance_error / max(abs(total_sink_current), 1e-30)
        ),
        "resistance_ohm": {
            "min": float(min(item[2] for item in resistors)),
            "max": float(max(item[2] for item in resistors)),
            "mean": float(np.mean([item[2] for item in resistors])),
        },
        "solved_voltage_V": {
            "min": float(solution.min()),
            "max": float(solution.max()),
        },
    }
    return node_voltage, statistics


def _iterm_coordinates(node: str) -> tuple[int | None, int | None]:
    match = re.search(r"_(-?\d+)_(-?\d+)$", node)
    return (
        (int(match.group(1)), int(match.group(2)))
        if match
        else (None, None)
    )


def build_ir_drop_rows(
    design: str,
    gate_names: list[str],
    gate_masters: list[str],
    network: dict[str, Any],
    node_voltage: dict[str, float],
    supply_voltage: float,
    dbu_per_micron: float,
    source_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """将SP sink按实例名对齐到基础图Gate顺序并保存原始物理量。"""

    if dbu_per_micron <= 0:
        raise ValueError("dbu_per_micron必须为正数")
    sinks_by_instance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unnamed_current_sources = 0
    for current in network["currents"]:
        instance = canonical_name(current["inst_name"])
        if not instance:
            unnamed_current_sources += 1
            continue
        node = current["node_positive"]
        if node not in node_voltage:
            continue
        voltage = float(node_voltage[node])
        drop_v = max(0.0, supply_voltage - voltage)
        sinks_by_instance[instance].append(
            {
                **current,
                "voltage_V": voltage,
                "ir_drop_V": drop_v,
            }
        )

    base_gate_set = set(gate_names)
    rows: list[dict[str, Any]] = []
    valid_drops: list[float] = []
    duplicate_instance_sinks = 0
    for name, master in zip(gate_names, gate_masters):
        candidates = sinks_by_instance.get(canonical_name(name), [])
        if len(candidates) > 1:
            duplicate_instance_sinks += 1
        # 多VDD端子时保留最低局部VDD，即最坏IR drop。
        selected = max(candidates, key=lambda item: item["ir_drop_V"]) if candidates else None
        valid = int(selected is not None)
        if selected:
            current_a = float(selected["current_A"])
            voltage_v = float(selected["voltage_V"])
            drop_v = float(selected["ir_drop_V"])
            node = str(selected["node_positive"])
            pin = str(selected["pin_name"])
            x_dbu, y_dbu = _iterm_coordinates(node)
            apparent_r = drop_v / current_a if current_a > 0 else float("nan")
            valid_drops.append(drop_v * 1000.0)
        else:
            current_a = voltage_v = drop_v = apparent_r = float("nan")
            node = pin = ""
            x_dbu = y_dbu = None
        rows.append(
            {
                "graph_id": design,
                "inst_name": name,
                "master": master,
                "vdd_pin": pin,
                "vdd_node": node,
                "x_dbu": x_dbu if x_dbu is not None else "",
                "y_dbu": y_dbu if y_dbu is not None else "",
                "x_um": x_dbu / dbu_per_micron if x_dbu is not None else float("nan"),
                "y_um": y_dbu / dbu_per_micron if y_dbu is not None else float("nan"),
                "current_A": current_a,
                "voltage_V": voltage_v,
                "ir_drop_V": drop_v,
                "ir_drop_mV": drop_v * 1000.0,
                "apparent_drop_per_cell_current_ohm": apparent_r,
                "valid": valid,
                "label_unit": "mV",
                "label_transform": "none",
                "label_definition": "1000*(VDD_source_V-VDD_gate_node_V)",
                "label_source_stage": "post-route/pdnsim-vdd-sp",
                "label_source_path": str(source_path),
            }
        )

    drops = np.asarray(valid_drops, dtype=np.float64)
    summary = {
        "gate_count": len(gate_names),
        "valid_gate_count": int(len(drops)),
        "missing_gate_count": len(gate_names) - int(len(drops)),
        "missing_gate_examples": [
            name for name in gate_names if canonical_name(name) not in sinks_by_instance
        ][:20],
        "sp_sink_instance_count": len(sinks_by_instance),
        "sp_extra_instance_count": len(set(sinks_by_instance) - base_gate_set),
        "sp_extra_instance_examples": sorted(set(sinks_by_instance) - base_gate_set)[:20],
        "unnamed_current_source_count": unnamed_current_sources,
        "duplicate_instance_sink_count": duplicate_instance_sinks,
        "ir_drop_mV": {
            "min": float(drops.min()) if drops.size else None,
            "max": float(drops.max()) if drops.size else None,
            "mean": float(drops.mean()) if drops.size else None,
            "std": float(drops.std()) if drops.size else None,
            "p50": float(np.percentile(drops, 50)) if drops.size else None,
            "p95": float(np.percentile(drops, 95)) if drops.size else None,
            "p99": float(np.percentile(drops, 99)) if drops.size else None,
        },
    }
    return rows, summary


def _csv_float(row: dict[str, str], *columns: str) -> float:
    for column in columns:
        value = row.get(column, "")
        if value is None or not str(value).strip():
            continue
        try:
            parsed = float(value)
        except ValueError:
            continue
        if math.isfinite(parsed):
            return parsed
    return float("nan")


def _csv_bool(value: str, default: bool = True) -> bool:
    clean = str(value or "").strip().lower()
    if not clean:
        return default
    if clean in {"1", "true", "yes", "y"}:
        return True
    if clean in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"无法解析布尔值: {value!r}")


def build_ir_drop_rows_from_csv(
    *,
    design: str,
    gate_names: list[str],
    gate_masters: list[str],
    source_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把外部PDNSim IRDrop CSV按Gate实例名规范化到流水线契约。

    外部文件保留 ``Voltage_V/IR_Drop/IR_Drop_mV/P95_mV/label`` 原值。
    若外部文件没有 ``current_A``，该列保持NaN，而不是伪造电流。模型前的数据
    变换仍由训练数据加载器负责。
    """

    with source_path.open("r", encoding="utf-8", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    indexed: dict[str, dict[str, str]] = {}
    duplicates: list[str] = []
    for row in source_rows:
        name = canonical_name(
            row.get("Cell") or row.get("inst_name") or row.get("cell") or ""
        )
        if not name:
            continue
        if name in indexed:
            duplicates.append(name)
        indexed[name] = row
    if duplicates:
        raise ValueError(
            f"外部IRDrop CSV存在重复Gate实例，例如 {duplicates[:10]}"
        )

    rows: list[dict[str, Any]] = []
    valid_values: list[float] = []
    gate_set = {canonical_name(name) for name in gate_names}
    for name, master in zip(gate_names, gate_masters):
        source = indexed.get(canonical_name(name))
        if source is None:
            voltage_v = drop_mv = p95_mv = source_label = current_a = float("nan")
            x_um = y_um = float("nan")
            valid = 0
            source_design = source_has_irdrop = ""
        else:
            voltage_v = _csv_float(source, "Voltage_V", "voltage_V")
            drop_mv = _csv_float(source, "IR_Drop_mV", "ir_drop_mV")
            if not math.isfinite(drop_mv):
                # 现有PDNSim表中的IR_Drop与IR_Drop_mV都以mV保存。
                drop_mv = _csv_float(source, "IR_Drop", "label")
            p95_mv = _csv_float(source, "P95_mV", "p95_mV")
            source_label = _csv_float(source, "label")
            current_a = _csv_float(source, "current_A", "Current_A")
            x_um = _csv_float(source, "X", "x_um")
            y_um = _csv_float(source, "Y", "y_um")
            source_has_irdrop = source.get("has_irdrop", "")
            valid = int(
                _csv_bool(source_has_irdrop, default=True)
                and math.isfinite(drop_mv)
                and drop_mv >= 0
            )
            source_design = source.get("Design", source.get("graph_id", ""))
            if valid:
                valid_values.append(drop_mv)
        drop_v = drop_mv / 1000.0 if math.isfinite(drop_mv) else float("nan")
        apparent_r = (
            drop_v / current_a
            if math.isfinite(current_a) and current_a > 0
            else float("nan")
        )
        rows.append(
            {
                "graph_id": design,
                "inst_name": name,
                "master": master,
                "vdd_pin": "",
                "vdd_node": "",
                "x_dbu": "",
                "y_dbu": "",
                "x_um": x_um,
                "y_um": y_um,
                "current_A": current_a,
                "voltage_V": voltage_v,
                "ir_drop_V": drop_v,
                "ir_drop_mV": drop_mv,
                "apparent_drop_per_cell_current_ohm": apparent_r,
                "source_design": source_design,
                "source_IR_Drop": (
                    source.get("IR_Drop", "") if source is not None else ""
                ),
                "source_P95_mV": p95_mv,
                "source_label": source_label,
                "source_has_irdrop": source_has_irdrop,
                "valid": valid,
                "label_unit": "mV",
                "label_transform": "none",
                "label_definition": "PDNSim CSV IR_Drop_mV raw value",
                "label_source_stage": "post-route/pdnsim-csv",
                "label_source_path": str(source_path),
            }
        )

    values = np.asarray(valid_values, dtype=np.float64)
    missing = [name for name in gate_names if canonical_name(name) not in indexed]
    extras = sorted(set(indexed) - gate_set)
    summary = {
        "schema": "r2g2_gate_irdrop_summary_v2",
        "design": design,
        "power_net": "VDD",
        "source_csv": str(source_path),
        "source_row_count": len(source_rows),
        "label_column": "ir_drop_mV",
        "label_transform": "none",
        "gate_alignment": {
            "gate_count": len(gate_names),
            "valid_gate_count": int(len(values)),
            "missing_gate_count": len(missing),
            "missing_gate_examples": missing[:20],
            "extra_instance_count": len(extras),
            "extra_instance_examples": extras[:20],
        },
        "ir_drop_mV": {
            "min": float(values.min()) if values.size else None,
            "max": float(values.max()) if values.size else None,
            "mean": float(values.mean()) if values.size else None,
            "std": float(values.std()) if values.size else None,
            "p50": float(np.percentile(values, 50)) if values.size else None,
            "p95": float(np.percentile(values, 95)) if values.size else None,
            "p99": float(np.percentile(values, 99)) if values.size else None,
        },
    }
    return rows, summary


def compare_ir_drop_rows(
    generated_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    *,
    absolute_tolerance_mV: float,
) -> dict[str, Any]:
    """只核验SP重算结果与外部CSV；外部CSV绝不参与正式标签生成。"""

    generated = {
        canonical_name(str(row["inst_name"])): row for row in generated_rows
    }
    reference = {
        canonical_name(str(row["inst_name"])): row for row in reference_rows
    }
    generated_keys = set(generated)
    reference_keys = set(reference)
    value_mismatches: list[dict[str, Any]] = []
    valid_mismatches: list[str] = []
    absolute_errors: list[float] = []
    compared = 0
    for name in sorted(generated_keys & reference_keys):
        actual = generated[name]
        expected = reference[name]
        actual_valid = bool(actual.get("valid", 0))
        expected_valid = bool(expected.get("valid", 0))
        if actual_valid != expected_valid:
            valid_mismatches.append(name)
            continue
        if not actual_valid:
            continue
        actual_value = float(actual["ir_drop_mV"])
        expected_value = float(expected["ir_drop_mV"])
        error = abs(actual_value - expected_value)
        absolute_errors.append(error)
        compared += 1
        if error > absolute_tolerance_mV and len(value_mismatches) < 20:
            value_mismatches.append(
                {
                    "inst_name": name,
                    "generated_mV": actual_value,
                    "reference_mV": expected_value,
                    "absolute_error_mV": error,
                }
            )

    missing_keys = sorted(reference_keys - generated_keys)
    extra_keys = sorted(generated_keys - reference_keys)
    mismatch_count = sum(
        1 for error in absolute_errors if error > absolute_tolerance_mV
    )
    return {
        "reference_only_not_label_source": True,
        "absolute_tolerance_mV": absolute_tolerance_mV,
        "compared_valid_gates": compared,
        "exact_within_tolerance": (
            not missing_keys
            and not extra_keys
            and not valid_mismatches
            and mismatch_count == 0
        ),
        "missing_keys": len(missing_keys),
        "extra_keys": len(extra_keys),
        "valid_mismatches": len(valid_mismatches),
        "value_mismatches": mismatch_count,
        "max_absolute_error_mV": (
            max(absolute_errors) if absolute_errors else None
        ),
        "mean_absolute_error_mV": (
            float(np.mean(absolute_errors)) if absolute_errors else None
        ),
        "missing_examples": missing_keys[:20],
        "extra_examples": extra_keys[:20],
        "valid_mismatch_examples": valid_mismatches[:20],
        "value_mismatch_examples": value_mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="提取Net线长、Gate拥塞和Gate VDD IRDrop原始标签"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument(
        "--congestion-radius",
        type=int,
        default=None,
        help="GCell高斯邻域半径；默认读取配置，0表示不平滑",
    )
    parser.add_argument(
        "--irdrop-reference-csv",
        "--irdrop-csv",
        dest="irdrop_reference_csv",
        default="",
        help=(
            "可选的旧IRDrop CSV，只用于与SP重算结果交叉核验，"
            "绝不作为正式标签来源"
        ),
    )
    parser.add_argument(
        "--irdrop-sp",
        default="",
        help="PDNSim生成的VDD_extracted.sp；IRDrop正式原始输入",
    )
    parser.add_argument(
        "--irdrop-vss-sp",
        default="",
        help="PDNSim生成的VSS_extracted.sp；当前仅记录来源，不参与VDD压降计算",
    )
    parser.add_argument(
        "--irdrop-supply-voltage",
        type=float,
        default=None,
        help="可选VDD电压校验值；默认从SP电压源推导",
    )
    parser.add_argument(
        "--irdrop-solver",
        choices=["direct", "cg"],
        default="direct",
        help="VDD稀疏求解器；direct为默认SuperLU，cg用于超大图低内存模式",
    )
    parser.add_argument(
        "--irdrop-solver-rtol",
        type=float,
        default=1e-10,
        help="VDD求解残差容差；CG用于停止条件，direct用于结果审计",
    )
    parser.add_argument(
        "--irdrop-solver-maxiter",
        type=int,
        default=20000,
        help="VDD稀疏共轭梯度最大迭代次数",
    )
    parser.add_argument(
        "--skip-irdrop",
        action="store_true",
        help="即使配置了VDD SP也跳过IRDrop标签",
    )
    parser.add_argument(
        "--timing-max-rpt",
        default="",
        help="OpenSTA最长/setup路径报告；优先于配置timing_max_rpt",
    )
    parser.add_argument(
        "--timing-min-rpt",
        default="",
        help="OpenSTA最短/hold路径报告；优先于配置timing_min_rpt",
    )
    parser.add_argument(
        "--timing-manifest",
        default="",
        help=(
            "OpenSTA V3来源清单；正式V2数据集用它核验Raw Route DEF、"
            "Raw SPEF、审计SDC与max/min报告SHA256"
        ),
    )
    parser.add_argument(
        "--skip-timing",
        action="store_true",
        help="即使配置了OpenSTA报告也跳过时序标签",
    )
    parser.add_argument(
        "--allow-partial-timing",
        action="store_true",
        help="仅用于审计/调试：允许低于配置阈值的部分时序实体对齐",
    )
    parser.add_argument(
        "--emit-timing-path-edges",
        action="store_true",
        help=(
            "额外输出报告采样路径的Pin/IO Pin边，仅供审计或旧实验；"
            "通用数据集默认只保留节点setup/hold slack"
        ),
    )
    parser.add_argument(
        "--rc-reference-dir",
        "--rc-dir",
        dest="rc_reference_dir",
        default="",
        help=(
            "单个设计的OpenRCX CSV目录，包含ground_cap.csv、"
            "net_coupling.csv和pin_net_resistance.csv；只做SPEF重算核验"
        ),
    )
    parser.add_argument(
        "--rc-spef",
        default="",
        help=(
            "直接提取Cg/Cc/Reff的同样本Raw后布线SPEF；"
            "优先于配置rc_spef/spef"
        ),
    )
    parser.add_argument(
        "--rc-source-mode",
        choices=["spef"],
        default="",
        help="正式RC标签来源固定为spef；该参数只用于显式声明",
    )
    parser.add_argument(
        "--skip-rc",
        action="store_true",
        help="即使配置了Raw后布线SPEF也跳过RC标签",
    )
    args = parser.parse_args()

    config_path, cfg = load_config(args.config)
    design = str(cfg.get("design_name") or config_path.stem)
    root_out = output_dir_from_config(config_path, cfg)
    base_path = (
        Path(args.base).resolve()
        if args.base
        else root_out / "base_graph" / "base_graph.pt"
    )
    base = torch.load(base_path, map_location="cpu", weights_only=False)
    label_def_value = str(
        cfg.get("label_def") or cfg.get("route_def") or ""
    )
    label_def = resolve_path(config_path, label_def_value, True)
    assert label_def is not None
    label_source_stage = validate_label_stage(config_path, cfg, label_def)
    lef_raw = cfg.get("lef", [])
    lef_values = lef_raw if isinstance(lef_raw, list) else [lef_raw]
    lef_paths = [
        path
        for value in lef_values
        if (path := resolve_path(config_path, str(value), False))
        and path.is_file()
    ]

    feature_module = load_feature_module()
    # 标签网格必须与02的特征网格同源同值，否则04会硬失败。
    congestion_grid_um = feature_module.resolve_congestion_grid_um(cfg)
    route_snapshot = feature_module.parse_def(label_def)
    route_lineage = feature_module.build_base_to_stage_lineage(
        base, route_snapshot
    )
    lengths, uses = extract_wirelengths(label_def)
    wirelength_rows = []
    for name in base.net_names:
        lineage = route_lineage["records"][name]
        route_nets = list(lineage["stage_nets"])
        present_lengths = [
            lengths[route_net]
            for route_net in route_nets
            if route_net in lengths
        ]
        valid = int(
            bool(route_nets)
            and len(present_lengths) == len(route_nets)
            and int(lineage["lineage_valid"]) == 1
        )
        value = sum(present_lengths) if valid else float("nan")
        net_uses = {uses.get(route_net, "SIGNAL") for route_net in route_nets}
        net_use = (
            "CLOCK"
            if "CLOCK" in net_uses
            else uses.get(name, next(iter(sorted(net_uses)), "SIGNAL"))
        )
        wirelength_rows.append(
            {
                "graph_id": design,
                "net_name": name,
                "net_use": net_use,
                "wirelength_um": value,
                "valid": valid,
                "route_segment_net_count": int(lineage["stage_net_count"]),
                "route_direct_net_count": int(
                    lineage["direct_stage_net_count"]
                ),
                "route_inferred_backend_net_count": int(
                    lineage["inferred_backend_stage_net_count"]
                ),
                "route_anchor_coverage": float(lineage["anchor_coverage"]),
                "route_lineage_valid": int(lineage["lineage_valid"]),
                "label_unit": "um",
                "label_transform": "none",
                "label_definition": (
                    "sum of Manhattan routed lengths over all Route DEF "
                    "segments aligned to the canonical base Net"
                ),
                "label_source_stage": label_source_stage,
                "label_source_path": str(label_def),
            }
        )
    missing_nets = [
        row["net_name"] for row in wirelength_rows if not int(row["valid"])
    ]

    header = parse_def_header(label_def, congestion_grid_um)
    layer_info = parse_tech_lef(lef_paths)
    utilization = extract_grid_utilization(label_def, header, layer_info)
    components = parse_components(label_def)
    radius = (
        args.congestion_radius
        if args.congestion_radius is not None
        else int(cfg.get("congestion_radius", 0))
    )
    congestion_rows: list[dict[str, Any]] = []
    for name, master in zip(base.gate_names, base.gate_masters):
        component = components.get(name)
        valid = int(
            component is not None
            and component.get("x") is not None
            and component.get("y") is not None
        )
        grid_x = (
            (int(component["x"]) - header["die"][0]) // header["gcell_x"]
            if valid
            else -1
        )
        grid_y = (
            (int(component["y"]) - header["die"][1]) // header["gcell_y"]
            if valid
            else -1
        )
        value = (
            congestion_at(
                utilization,
                grid_x,
                grid_y,
                radius,
            )
            if valid
            else float("nan")
        )
        congestion_rows.append(
            {
                "graph_id": design,
                "inst_name": name,
                "master": master,
                "cell_congestion": value,
                "grid_x": grid_x,
                "grid_y": grid_y,
                "grid_step_x_um": header["gcell_x"] / header["dbu"],
                "grid_step_y_um": header["gcell_y"] / header["dbu"],
                "valid": valid,
                "label_unit": "dimensionless",
                "label_transform": "none",
                "label_definition": (
                    "max(horizontal_routed_demand/capacity,"
                    "vertical_routed_demand/capacity) on fixed "
                    f"{congestion_grid_um:g}um "
                    f"({header['gcell_x']}DBU at {header['dbu']:g}DBU/um) grid"
                ),
                "gate_to_grid_mapping": (
                    "trusted_post_route_gate_origin_fixed_"
                    f"{congestion_grid_um:g}um_grid"
                ),
                "label_source_stage": label_source_stage,
                "label_source_path": str(label_def),
            }
        )

    out_dir = (
        Path(args.out_dir).resolve()
        if args.out_dir
        else root_out / "labels"
    )
    ir_drop_rows: list[dict[str, Any]] | None = None
    ir_drop_summary: dict[str, Any] | None = None
    irdrop_sp_raw = args.irdrop_sp or str(cfg.get("irdrop_sp", ""))
    irdrop_vss_sp_raw = (
        args.irdrop_vss_sp or str(cfg.get("irdrop_vss_sp", ""))
    )
    irdrop_reference_csv_raw = (
        args.irdrop_reference_csv
        or str(cfg.get("irdrop_reference_csv", ""))
        or str(cfg.get("irdrop_csv", ""))
    )
    if irdrop_sp_raw and not args.skip_irdrop:
        irdrop_sp = resolve_path(config_path, irdrop_sp_raw, True)
        assert irdrop_sp is not None
        irdrop_vss_sp = (
            resolve_path(config_path, irdrop_vss_sp_raw, True)
            if irdrop_vss_sp_raw
            else None
        )
        print(f"[label][irdrop] parsing VDD network: {irdrop_sp}", flush=True)
        network = parse_vdd_spice(irdrop_sp)
        print(
            f"[label][irdrop] parsed: R={len(network['resistors'])} "
            f"I={len(network['currents'])} "
            f"V={len(network['fixed_voltages'])}; solving...",
            flush=True,
        )
        configured_supply = (
            args.irdrop_supply_voltage
            if args.irdrop_supply_voltage is not None
            else (
                float(cfg["irdrop_supply_voltage"])
                if "irdrop_supply_voltage" in cfg
                else None
            )
        )
        node_voltage, solver_summary = solve_vdd_network(
            network,
            supply_voltage=configured_supply,
            relative_tolerance=args.irdrop_solver_rtol,
            max_iterations=args.irdrop_solver_maxiter,
            solver=args.irdrop_solver,
        )
        print(
            "[label][irdrop] solved: "
            f"solver={solver_summary['solver']} "
            f"nodes={solver_summary['node_count']} "
            f"relative_residual={solver_summary['solver_relative_residual']:.3e}",
            flush=True,
        )
        supply_voltage = float(solver_summary["supply_voltage_V"])
        ir_drop_rows, alignment_summary = build_ir_drop_rows(
            design=design,
            gate_names=list(base.gate_names),
            gate_masters=list(base.gate_masters),
            network=network,
            node_voltage=node_voltage,
            supply_voltage=supply_voltage,
            dbu_per_micron=float(header["dbu"]),
            source_path=irdrop_sp,
        )
        ir_drop_summary = {
            "schema": "r2g2_gate_irdrop_summary_v1",
            "design": design,
            "power_net": "VDD",
            "source_sp": str(irdrop_sp),
            "source_sp_sha256": _sha256(irdrop_sp),
            "vss_source_sp": (
                str(irdrop_vss_sp) if irdrop_vss_sp is not None else ""
            ),
            "vss_source_sp_sha256": (
                _sha256(irdrop_vss_sp)
                if irdrop_vss_sp is not None
                else ""
            ),
            "vss_role": (
                "provenance_only_not_used_for_vdd_ir_drop"
                if irdrop_vss_sp is not None
                else "not_configured"
            ),
            "label_column": "ir_drop_mV",
            "label_transform": "none",
            "formal_label_source": "VDD_extracted.sp sparse KCL solve",
            "network_solver": solver_summary,
            "gate_alignment": alignment_summary,
        }
        if irdrop_reference_csv_raw:
            require_reference = bool(
                cfg.get("irdrop_require_reference_match", False)
            )
            reference_csv = resolve_path(
                config_path,
                irdrop_reference_csv_raw,
                require_reference,
            )
            assert reference_csv is not None
            if reference_csv.is_file():
                reference_rows, reference_summary = (
                    build_ir_drop_rows_from_csv(
                        design=design,
                        gate_names=list(base.gate_names),
                        gate_masters=list(base.gate_masters),
                        source_path=reference_csv,
                    )
                )
                tolerance = float(
                    cfg.get("irdrop_reference_abs_tolerance_mV", 0.001)
                )
                comparison = compare_ir_drop_rows(
                    ir_drop_rows,
                    reference_rows,
                    absolute_tolerance_mV=tolerance,
                )
                ir_drop_summary["reference_csv"] = {
                    "path": str(reference_csv),
                    "sha256": _sha256(reference_csv),
                    "summary": reference_summary,
                    "comparison": comparison,
                }
                print(
                    "[label][irdrop] reference CSV cross-check: "
                    f"exact={comparison['exact_within_tolerance']} "
                    f"max_abs_error_mV={comparison['max_absolute_error_mV']}",
                    flush=True,
                )
                if (
                    require_reference
                    and not comparison["exact_within_tolerance"]
                ):
                    raise ValueError(
                        "VDD SP重算IRDrop与参考CSV不一致；"
                        f"比较摘要={comparison}"
                    )
            else:
                ir_drop_summary["reference_csv"] = {
                    "path": str(reference_csv),
                    "available": False,
                    "required": False,
                    "skipped": True,
                }
                print(
                    "[label][irdrop] optional reference CSV absent; "
                    "SP-derived labels remain valid",
                    flush=True,
                )
    elif irdrop_reference_csv_raw and not args.skip_irdrop:
        raise ValueError(
            "配置了IRDrop参考CSV但没有irdrop_sp；正式标签必须从"
            "VDD_extracted.sp重算，禁止用旧CSV直接构图"
        )

    timing_tables: dict[str, list[dict[str, Any]]] | None = None
    timing_summary: dict[str, Any] | None = None
    timing_max_raw = args.timing_max_rpt or str(cfg.get("timing_max_rpt", ""))
    timing_min_raw = args.timing_min_rpt or str(cfg.get("timing_min_rpt", ""))
    timing_enabled = (
        bool(args.timing_max_rpt or args.timing_min_rpt)
        or bool(cfg.get("timing_enabled", True))
    )
    if bool(timing_max_raw) != bool(timing_min_raw):
        raise ValueError("timing_max_rpt与timing_min_rpt必须同时配置")
    if (
        timing_enabled
        and timing_max_raw
        and timing_min_raw
        and not args.skip_timing
    ):
        timing_max = resolve_path(config_path, timing_max_raw, True)
        timing_min = resolve_path(config_path, timing_min_raw, True)
        assert timing_max is not None and timing_min is not None
        timing_manifest_raw = (
            args.timing_manifest or str(cfg.get("timing_manifest", ""))
        )
        require_timing_manifest = bool(
            cfg.get("timing_require_manifest", False)
        )
        timing_contract: dict[str, Any] | None = None
        if timing_manifest_raw:
            timing_manifest = resolve_path(
                config_path, timing_manifest_raw, True
            )
            route_def = resolve_path(
                config_path, str(cfg.get("route_def", "")), True
            )
            timing_spef = resolve_path(
                config_path,
                str(cfg.get("spef", "") or cfg.get("rc_spef", "")),
                True,
            )
            timing_sdc = resolve_path(
                config_path, str(cfg.get("sdc", "")), True
            )
            assert (
                timing_manifest is not None
                and route_def is not None
                and timing_spef is not None
                and timing_sdc is not None
            )
            timing_contract = validate_timing_manifest(
                manifest_path=timing_manifest,
                sample_id=str(cfg.get("sample_id") or design),
                max_report=timing_max,
                min_report=timing_min,
                expected_inputs={
                    "route_def": route_def,
                    "spef": timing_spef,
                    "sdc": timing_sdc,
                },
            )
            print(
                "[label][timing] V3 source contract verified: "
                f"{timing_manifest}",
                flush=True,
            )
        elif require_timing_manifest:
            raise ValueError(
                "timing_require_manifest=true但未配置timing_manifest；"
                "正式V2数据集禁止只凭RPT存在性生成时序标签"
            )
        pin_keys = list(
            dict.fromkeys(
                zip(base.connection_inst_names, base.connection_pin_names)
            )
        )
        io_keys = list(base.io_pin_names)
        print(
            f"[label][timing] parsing OpenSTA reports: "
            f"{timing_max.name}, {timing_min.name}",
            flush=True,
        )
        timing_tables, timing_summary = build_aligned_tables(
            design=design,
            pin_keys=pin_keys,
            io_keys=io_keys,
            max_report=timing_max,
            min_report=timing_min,
            physical_instances=set(components),
        )
        if timing_contract is not None:
            timing_summary["source_contract"] = timing_contract

    rc_tables: dict[str, list[dict[str, Any]]] | None = None
    rc_summary: dict[str, Any] | None = None
    rc_dir_raw = (
        args.rc_reference_dir or str(cfg.get("rc_label_dir", ""))
    )
    rc_spef_raw = (
        args.rc_spef
        or str(cfg.get("rc_spef", ""))
        or str(cfg.get("spef", ""))
    )
    rc_source_mode = (
        args.rc_source_mode
        or str(cfg.get("rc_source_mode", "spef")).strip().lower()
    )
    if rc_source_mode != "spef":
        raise ValueError(
            "正式数据集禁止从旧RC CSV直接构图；"
            f"rc_source_mode必须是spef，当前为{rc_source_mode!r}"
        )
    if not args.skip_rc:
        if not rc_spef_raw:
            raise ValueError("rc_source_mode=spef但未配置rc_spef或spef")
        rc_spef = resolve_path(config_path, rc_spef_raw, True)
        assert rc_spef is not None
        rc_corner = str(cfg.get("rc_spef_corner", "typ")).strip().lower()
        rc_raw_dir = out_dir / "rc_raw_from_spef"
        print(
            f"[label][rc] extracting SPEF ({rc_corner}): {rc_spef}",
            flush=True,
        )
        rc_raw_dir, spef_summary = extract_rc_csv_from_spef(
            spef_path=rc_spef,
            output_dir=rc_raw_dir,
            design=design,
            corner=rc_corner,
            allow_truncated_tail=bool(
                cfg.get("rc_allow_truncated_spef_tail", False)
            ),
        )
        expected_dropped = cfg.get(
            "rc_truncated_spef_expected_dropped_net_count"
        )
        if expected_dropped is not None:
            actual_dropped = int(
                spef_summary["truncated_tail_recovery"][
                    "dropped_net_count"
                ]
            )
            if actual_dropped != int(expected_dropped):
                raise ValueError(
                    "SPEF尾部恢复数量与配置例外不一致: "
                    f"expected={expected_dropped} actual={actual_dropped}"
                )
        pin_keys = list(
            dict.fromkeys(
                zip(base.connection_inst_names, base.connection_pin_names)
            )
        )
        rc_tables, rc_summary = build_aligned_rc_tables(
            design=design,
            net_names=list(base.net_names),
            pin_keys=pin_keys,
            io_keys=list(base.io_pin_names),
            source_dir=rc_raw_dir,
            stage_to_base=route_lineage["stage_to_base"],
        )
        for rows in rc_tables.values():
            for row in rows:
                row["label_source_path"] = str(rc_spef)
        rc_summary["source_mode"] = "spef"
        rc_summary["spef_extraction"] = spef_summary
        if rc_dir_raw:
            require_reference = bool(
                cfg.get("rc_require_reference_match", False)
            )
            reference_dir = Path(rc_dir_raw)
            if not reference_dir.is_absolute():
                reference_dir = (
                    config_path.parent / reference_dir
                ).resolve()
            if not reference_dir.is_dir():
                if require_reference:
                    raise FileNotFoundError(reference_dir)
                rc_summary["reference_csv_comparison"] = {
                    "reference_dir": str(reference_dir),
                    "available": False,
                    "required": False,
                    "skipped": True,
                }
                print(
                    "[label][rc] optional reference CSV directory absent; "
                    "SPEF-derived labels remain valid",
                    flush=True,
                )
            else:
                comparison = compare_rc_csv_directories(
                    rc_raw_dir, reference_dir
                )
                rc_summary["reference_csv_comparison"] = comparison
                print(
                    "[label][rc] prepared CSV cross-check: "
                    f"exact={comparison['exact']} reference={reference_dir}",
                    flush=True,
                )
                if require_reference and not comparison["exact"]:
                    raise ValueError(
                        "SPEF直提RC与配置rc_label_dir不一致；"
                        f"比较摘要={comparison}"
                    )
    # 最终标签表按“写入哪类节点/边”组织，而不是按提取工具拆成多个文件。
    # 阶段4只需按稳定实体键直接连接这些表，不再跨多张同节点类型表做二次join。
    ir_drop_by_gate = (
        {
            row["inst_name"]: row
            for row in ir_drop_rows
        }
        if ir_drop_rows is not None
        else {}
    )
    gate_label_rows: list[dict[str, Any]] = []
    for congestion in congestion_rows:
        ir = ir_drop_by_gate.get(congestion["inst_name"], {})
        gate_label_rows.append(
            {
                "graph_id": congestion["graph_id"],
                "inst_name": congestion["inst_name"],
                "master": congestion["master"],
                "cell_congestion": congestion["cell_congestion"],
                "congestion_grid_x": congestion["grid_x"],
                "congestion_grid_y": congestion["grid_y"],
                "grid_step_x_um": congestion["grid_step_x_um"],
                "grid_step_y_um": congestion["grid_step_y_um"],
                "congestion_valid": congestion["valid"],
                "congestion_label_unit": congestion["label_unit"],
                "congestion_label_transform": congestion["label_transform"],
                "congestion_label_definition": congestion["label_definition"],
                "gate_to_grid_mapping": congestion["gate_to_grid_mapping"],
                "congestion_label_source_stage": congestion[
                    "label_source_stage"
                ],
                "congestion_label_source_path": congestion[
                    "label_source_path"
                ],
                "vdd_pin": ir.get("vdd_pin", ""),
                "vdd_node": ir.get("vdd_node", ""),
                "x_dbu": ir.get("x_dbu", ""),
                "y_dbu": ir.get("y_dbu", ""),
                "current_A": ir.get("current_A", ""),
                "voltage_V": ir.get("voltage_V", ""),
                "ir_drop_V": ir.get("ir_drop_V", ""),
                "ir_drop_mV": ir.get("ir_drop_mV", ""),
                "apparent_drop_per_cell_current_ohm": ir.get(
                    "apparent_drop_per_cell_current_ohm", ""
                ),
                "irdrop_valid": ir.get("valid", 0),
                "irdrop_label_unit": ir.get("label_unit", "mV"),
                "irdrop_label_transform": ir.get(
                    "label_transform", "none"
                ),
                "irdrop_label_definition": ir.get("label_definition", ""),
                "irdrop_label_source_stage": ir.get(
                    "label_source_stage", ""
                ),
                "irdrop_label_source_path": ir.get("label_source_path", ""),
            }
        )
    if ir_drop_rows is not None:
        extra_ir_gates = set(ir_drop_by_gate) - {
            row["inst_name"] for row in congestion_rows
        }
        if extra_ir_gates:
            raise ValueError(
                "IRDrop表存在基础Gate标签表外实体: "
                f"{sorted(extra_ir_gates)[:5]}"
            )
    write_rows(
        out_dir / "gate_con_IR.csv",
        [
            "graph_id",
            "inst_name",
            "master",
            "cell_congestion",
            "congestion_grid_x",
            "congestion_grid_y",
            "grid_step_x_um",
            "grid_step_y_um",
            "congestion_valid",
            "congestion_label_unit",
            "congestion_label_transform",
            "congestion_label_definition",
            "gate_to_grid_mapping",
            "congestion_label_source_stage",
            "congestion_label_source_path",
            "vdd_pin",
            "vdd_node",
            "x_dbu",
            "y_dbu",
            "current_A",
            "voltage_V",
            "ir_drop_V",
            "ir_drop_mV",
            "apparent_drop_per_cell_current_ohm",
            "irdrop_valid",
            "irdrop_label_unit",
            "irdrop_label_transform",
            "irdrop_label_definition",
            "irdrop_label_source_stage",
            "irdrop_label_source_path",
        ],
        gate_label_rows,
    )

    ground_cap_by_net = (
        {
            row["net_name"]: row
            for row in rc_tables["ground_cap"]
        }
        if rc_tables is not None
        else {}
    )
    net_label_rows: list[dict[str, Any]] = []
    for wire in wirelength_rows:
        ground = ground_cap_by_net.get(wire["net_name"], {})
        net_label_rows.append(
            {
                "graph_id": wire["graph_id"],
                "net_name": wire["net_name"],
                "net_use": wire["net_use"],
                "wirelength_um": wire["wirelength_um"],
                "wirelength_valid": wire["valid"],
                "route_segment_net_count": wire[
                    "route_segment_net_count"
                ],
                "route_direct_net_count": wire["route_direct_net_count"],
                "route_inferred_backend_net_count": wire[
                    "route_inferred_backend_net_count"
                ],
                "route_anchor_coverage": wire["route_anchor_coverage"],
                "route_lineage_valid": wire["route_lineage_valid"],
                "wirelength_label_unit": wire["label_unit"],
                "wirelength_label_transform": wire["label_transform"],
                "wirelength_label_definition": wire["label_definition"],
                "wirelength_label_source_stage": wire[
                    "label_source_stage"
                ],
                "wirelength_label_source_path": wire["label_source_path"],
                "ground_cap_pF": ground.get("ground_cap_pF", ""),
                "ground_cap_valid": ground.get("valid", 0),
                "ground_cap_label_unit": ground.get("label_unit", "pF"),
                "ground_cap_label_transform": ground.get(
                    "label_transform", "none"
                ),
                "ground_cap_label_definition": ground.get(
                    "label_definition", ""
                ),
                "ground_cap_label_source_stage": ground.get(
                    "label_source_stage", ""
                ),
                "ground_cap_label_source_path": ground.get(
                    "label_source_path", ""
                ),
            }
        )
    if ground_cap_by_net:
        extra_ground_nets = set(ground_cap_by_net) - {
            row["net_name"] for row in wirelength_rows
        }
        if extra_ground_nets:
            raise ValueError(
                "Cg表存在基础Net标签表外实体: "
                f"{sorted(extra_ground_nets)[:5]}"
            )
    write_rows(
        out_dir / "net_wirelength_Cg.csv",
        [
            "graph_id",
            "net_name",
            "net_use",
            "wirelength_um",
            "wirelength_valid",
            "route_segment_net_count",
            "route_direct_net_count",
            "route_inferred_backend_net_count",
            "route_anchor_coverage",
            "route_lineage_valid",
            "wirelength_label_unit",
            "wirelength_label_transform",
            "wirelength_label_definition",
            "wirelength_label_source_stage",
            "wirelength_label_source_path",
            "ground_cap_pF",
            "ground_cap_valid",
            "ground_cap_label_unit",
            "ground_cap_label_transform",
            "ground_cap_label_definition",
            "ground_cap_label_source_stage",
            "ground_cap_label_source_path",
        ],
        net_label_rows,
    )

    if ir_drop_rows is not None and ir_drop_summary is not None:
        summary_path = out_dir / "ir_drop.summary.json"
        _write_json(summary_path, ir_drop_summary)
        print(f"[label] {summary_path.name}: source/alignment statistics")

    if timing_tables is not None and timing_summary is not None:
        timing_summary_path = out_dir / "timing.summary.json"
        _write_json(timing_summary_path, timing_summary)
        alignment = timing_summary["alignment"]
        threshold = float(cfg.get("timing_min_alignment_ratio", 0.95))
        actual_ratio = min(
            float(alignment["source_consistency_ratio"]),
            float(alignment["path_source_consistency_ratio"]),
        )
        allow_partial = args.allow_partial_timing or bool(
            cfg.get("timing_allow_partial", False)
        )
        if actual_ratio < threshold and not allow_partial:
            raise ValueError(
                "OpenSTA报告与基础图实体版本不一致，拒绝组合部分时序图: "
                f"source_ratio={alignment['source_consistency_ratio']:.4f}, "
                f"path_ratio={alignment['path_source_consistency_ratio']:.4f}, "
                f"required>={threshold:.4f}。详细审计见 {timing_summary_path}；"
                "如仅做诊断可使用--allow-partial-timing。"
            )

        node_fields = [
            "graph_id",
            "inst_name",
            "pin_name",
            "setup_slack_ns",
            "hold_slack_ns",
            "setup_valid",
            "hold_valid",
            "is_timing_endpoint",
            "label_unit",
            "label_transform",
            "label_source_stage",
            "label_source_path",
        ]
        write_rows(
            out_dir / "pin_timing.csv",
            node_fields,
            timing_tables["pin_timing"],
        )
        write_rows(
            out_dir / "iopin_timing.csv",
            [
                field
                for field in node_fields
                if field not in {"inst_name", "pin_name"}
            ][:1]
            + ["iopin_name"]
            + [
                field
                for field in node_fields
                if field not in {"graph_id", "inst_name", "pin_name"}
            ],
            timing_tables["iopin_timing"],
        )
        emit_timing_edges = args.emit_timing_path_edges or bool(
            cfg.get("timing_emit_report_path_edges", False)
        )
        if emit_timing_edges:
            timing_edge_rows: list[dict[str, Any]] = []
            for source_type, _, target_type in TIMING_RELATIONS:
                relation_key = f"{source_type}|timing_path|{target_type}"
                for row in timing_tables[relation_key]:
                    timing_edge_rows.append(
                        {
                            "graph_id": row["graph_id"],
                            "src_node_type": source_type,
                            "src_inst_name": row.get("src_inst_name", ""),
                            "src_pin_name": row.get("src_pin_name", ""),
                            "src_iopin_name": row.get("src_iopin_name", ""),
                            "dst_node_type": target_type,
                            "dst_inst_name": row.get("dst_inst_name", ""),
                            "dst_pin_name": row.get("dst_pin_name", ""),
                            "dst_iopin_name": row.get("dst_iopin_name", ""),
                            "setup_delay_ns": row["setup_delay_ns"],
                            "hold_delay_ns": row["hold_delay_ns"],
                            "setup_valid": row["setup_valid"],
                            "hold_valid": row["hold_valid"],
                            "label_unit": row["label_unit"],
                            "label_transform": row["label_transform"],
                            "label_source_stage": row["label_source_stage"],
                            "label_source_path": row["label_source_path"],
                        }
                    )
            write_rows(
                out_dir / "edges_timing_path.csv",
                [
                    "graph_id",
                    "src_node_type",
                    "src_inst_name",
                    "src_pin_name",
                    "src_iopin_name",
                    "dst_node_type",
                    "dst_inst_name",
                    "dst_pin_name",
                    "dst_iopin_name",
                    "setup_delay_ns",
                    "hold_delay_ns",
                    "setup_valid",
                    "hold_valid",
                    "label_unit",
                    "label_transform",
                    "label_source_stage",
                    "label_source_path",
                ],
                timing_edge_rows,
            )
        else:
            print(
                "[label][timing] report-path edges disabled; "
                "formal labels are Pin/IO Pin setup/hold slack only"
            )
        print(
            "[label][timing] alignment accepted: "
            f"source={alignment['source_consistency_ratio']:.4f} "
            f"path={alignment['path_source_consistency_ratio']:.4f}"
        )
    if rc_tables is not None and rc_summary is not None:
        write_rows(
            out_dir / "edges_net_net_Cc.csv",
            [
                "graph_id",
                "net1_name",
                "net2_name",
                "coupling_cap_pF",
                "valid",
                "label_unit",
                "label_transform",
                "label_definition",
                "label_source_stage",
                "label_source_path",
                "source_design",
            ],
            rc_tables["net_coupling"],
        )
        write_rows(
            out_dir / "edges_pin_pin_Reff.csv",
            [
                "graph_id",
                "net_name",
                "src_node_type",
                "src_inst_name",
                "src_pin_name",
                "src_iopin_name",
                "dst_node_type",
                "dst_inst_name",
                "dst_pin_name",
                "dst_iopin_name",
                "effective_resistance_ohm",
                "valid",
                "label_unit",
                "label_transform",
                "label_definition",
                "label_source_stage",
                "label_source_path",
                "source_design",
            ],
            rc_tables["pin_net_resistance"],
        )
        rc_summary_path = out_dir / "rc.summary.json"
        _write_json(rc_summary_path, rc_summary)
        print(
            "[label][rc] aligned: "
            f"Cg={rc_summary['ground_cap']['aligned_valid_nets']} "
            f"Cc_pairs={rc_summary['coupling_cap']['aligned_unordered_pairs']} "
            f"Reff={rc_summary['effective_resistance']['aligned_directed_edges']}"
        )
    print(
        f"[label] completed: {out_dir}; grids={len(utilization)} "
        f"routing_layers={len(layer_info)} congestion_radius={radius} "
        f"missing_route_nets={len(missing_nets)} "
        f"irdrop={'generated' if ir_drop_rows is not None else 'skipped'} "
        f"timing={'generated' if timing_tables is not None else 'skipped'} "
        f"rc={'generated' if rc_tables is not None else 'skipped'}"
    )


if __name__ == "__main__":
    main()
