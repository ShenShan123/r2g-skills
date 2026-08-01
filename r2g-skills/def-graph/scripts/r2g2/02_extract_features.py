#!/usr/bin/env python3
"""阶段2：按因果时点生成四组、每组8张特征CSV。

四组数据集按待预测阶段命名，特征截止点固定为：

* floorplan <- post-Yosys（不读取任何DEF）
* placement <- Floorplan DEF
* cts       <- Placement DEF
* route     <- CTS DEF

四阶段共享阶段1综合网表基础拓扑。每张表严格输出相同的Canonical实体；当前阶段
尚不可用或DEF中不存在的物理量写NaN，对应valid列写0。Routing DEF只属于阶段3
label来源，本脚本从配置解析到执行均禁止读取它。post-floorplan中未完成标准单元
放置的临时重合坐标不属于有效物理信息，只保留FIXED宏和IO位置。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import torch


POWER_PINS = {"VDD", "VPWR", "VCC", "VCCA", "VCCD"}
GROUND_PINS = {"VSS", "VGND", "GND", "VSSA", "VSSD"}

FAST_ROUTE_GRID_TRACKS = 15
METAL3_PITCH_UM = 0.14
FIXED_CONGESTION_GRID_UM = FAST_ROUTE_GRID_TRACKS * METAL3_PITCH_UM


def resolve_congestion_grid_um(cfg: dict[str, Any]) -> float:
    """返回本样本的固定拥塞GCell边长(um)。

    r2g-skills delta vs upstream R2G2.0: 上游把``15 × 0.14um``写死在02/03/04
    三个文件里。0.14um是Nangate45的Metal3 pitch, 对sky130hd/sky130hs/gf180/
    ihp-sg13g2都不成立, 会让"技术库预先确定的网格"变成另一个工艺的常量。
    这里把它变成可由配置显式给出的量, 默认值仍是Nangate45的2.1um, 因此
    Nangate45行为与上游逐字节一致。``make_sample_config.py``会从平台tech LEF
    的Metal3(或等价第三布线层) pitch自动填入``congestion_grid_pitch_um``。
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


PREDICTION_STAGES = ("floorplan", "placement", "cts", "route")
STAGE_INPUTS = {
    "floorplan": (None, None, "post_yosys"),
    "placement": ("floorplan_def", "floorplan_def", "floorplan"),
    "cts": ("place_def", "placement_def", "placement"),
    "route": ("cts_def", "cts_def", "cts"),
}
TRUSTED_STANDARD_CELL_POSITION_STAGES = {"cts", "route"}
TRUSTED_FLOORPLAN_MACRO_CLASSES = {"BLOCK", "PAD", "RING", "COVER"}


# ===== 阶段2内置：Canonical base Net到后端物理Net的稳定对齐 =====

Endpoint = tuple[str, ...]


def endpoint_key(instance: str, pin_name: str) -> Endpoint:
    """返回跨综合网表/DEF稳定的Pin实体键。"""

    return (
        ("io_pin", pin_name)
        if instance == "PIN"
        else ("pin", instance, pin_name)
    )


def stage_endpoint_to_net(
    snapshot: dict[str, Any],
) -> dict[Endpoint, str]:
    """建立阶段DEF稳定端点到物理Net的一对一映射。"""

    mapping: dict[Endpoint, str] = {}
    conflicts: list[tuple[Endpoint, str, str]] = []
    for net_name, info in snapshot["nets"].items():
        for instance, pin_name in info.get("connections", []):
            key = endpoint_key(str(instance), str(pin_name))
            previous = mapping.get(key)
            if previous is not None and previous != net_name:
                conflicts.append((key, previous, net_name))
            mapping[key] = str(net_name)
    for io_name, info in snapshot["iopins"].items():
        net_name = str(info.get("net", ""))
        if not net_name:
            continue
        key = ("io_pin", str(io_name))
        previous = mapping.get(key)
        if previous is not None and previous != net_name:
            conflicts.append((key, previous, net_name))
        mapping[key] = net_name
    if conflicts:
        raise ValueError(
            f"阶段DEF同一稳定端点连接多个Net，例如: {conflicts[:10]}"
        )
    return mapping


def base_endpoints_by_net(base: Any) -> dict[str, list[Endpoint]]:
    """按Canonical base Net收集内部Pin和顶层IO稳定端点。"""

    result: dict[str, list[Endpoint]] = defaultdict(list)
    for net_name, instance, pin_name in zip(
        base.connection_net_names,
        base.connection_inst_names,
        base.connection_pin_names,
    ):
        result[str(net_name)].append(
            ("pin", str(instance), str(pin_name))
        )
    for io_name, net_name in zip(
        base.io_pin_names, base.io_pin_net_names
    ):
        result[str(net_name)].append(("io_pin", str(io_name)))
    return dict(result)


class _NetUnionFind:
    """连接被后端透明BUF/INV拆开的物理Net分量。"""

    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def build_base_to_stage_lineage(
    base: Any,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """返回base Net到阶段物理Net的直接端点+透明组件扩展映射。

    Placement/CTS/Route可能用新增BUF/INV把综合网拆成多条物理网。该映射以
    ``(inst_name, pin_name)`` 和顶层 ``iopin_name`` 为稳定锚点，再沿后端新增的
    透明BUF/INV连接分量扩展。宏单元自身的多Pin拆网通过每个稳定Pin直接反标；
    非透明新增单元不跨接，避免把多个Canonical Net错误合并。
    """

    base_net_names = [str(name) for name in base.net_names]
    base_gate_names = {str(name) for name in base.gate_names}
    stage_net_names = sorted(str(name) for name in snapshot["nets"])
    stage_endpoint_map = stage_endpoint_to_net(snapshot)
    endpoints_by_base = base_endpoints_by_net(base)

    union_find = _NetUnionFind(stage_net_names)
    nets_by_added_component: dict[str, set[str]] = defaultdict(set)
    transparent_added_components: set[str] = set()
    ignored_nontransparent_added_components: set[str] = set()
    for instance, info in snapshot["components"].items():
        instance = str(instance)
        if instance in base_gate_names:
            continue
        master = str(info.get("master", "")).upper()
        if "BUF" in master or "INV" in master:
            transparent_added_components.add(instance)
        else:
            ignored_nontransparent_added_components.add(instance)
    for stage_net, info in snapshot["nets"].items():
        for instance, _ in info.get("connections", []):
            instance = str(instance)
            if instance in transparent_added_components:
                nets_by_added_component[instance].add(str(stage_net))
    for nets in nets_by_added_component.values():
        ordered = sorted(nets)
        for stage_net in ordered[1:]:
            union_find.union(ordered[0], stage_net)

    component_members: dict[str, set[str]] = defaultdict(set)
    for stage_net in stage_net_names:
        component_members[union_find.find(stage_net)].add(stage_net)

    direct_stage_nets: dict[str, set[str]] = defaultdict(set)
    mapped_endpoint_count: dict[str, int] = defaultdict(int)
    root_base_anchors: dict[str, set[str]] = defaultdict(set)
    for base_net in base_net_names:
        for endpoint in endpoints_by_base.get(base_net, []):
            stage_net = stage_endpoint_map.get(endpoint)
            if stage_net is None:
                continue
            direct_stage_nets[base_net].add(stage_net)
            mapped_endpoint_count[base_net] += 1
            root_base_anchors[union_find.find(stage_net)].add(base_net)

    records: dict[str, dict[str, Any]] = {}
    stage_to_base_candidates: dict[str, set[str]] = defaultdict(set)
    for base_net in base_net_names:
        direct = set(direct_stage_nets.get(base_net, set()))
        assigned = set(direct)
        ambiguous_roots: set[str] = set()
        inferred: set[str] = set()
        for stage_net in direct:
            root = union_find.find(stage_net)
            anchors = root_base_anchors[root]
            if anchors == {base_net}:
                inferred.update(component_members[root] - direct)
                assigned.update(component_members[root])
            elif len(anchors) > 1:
                ambiguous_roots.add(root)
        for stage_net in assigned:
            stage_to_base_candidates[stage_net].add(base_net)
        endpoints = endpoints_by_base.get(base_net, [])
        anchor_count = mapped_endpoint_count.get(base_net, 0)
        records[base_net] = {
            "base_net": base_net,
            "base_endpoint_count": len(endpoints),
            "mapped_base_endpoint_count": anchor_count,
            "anchor_coverage": (
                anchor_count / len(endpoints) if endpoints else 0.0
            ),
            "direct_stage_nets": sorted(direct),
            "direct_stage_net_count": len(direct),
            "inferred_backend_stage_nets": sorted(inferred),
            "inferred_backend_stage_net_count": len(inferred),
            "stage_nets": sorted(assigned),
            "stage_net_count": len(assigned),
            "split_flag": int(len(assigned) > 1),
            "renamed_flag": int(
                len(assigned) == 1 and next(iter(assigned)) != base_net
            ),
            "ambiguous_component_count": len(ambiguous_roots),
            "lineage_valid": int(bool(assigned) and not ambiguous_roots),
        }

    stage_to_base = {
        stage_net: next(iter(candidates))
        for stage_net, candidates in stage_to_base_candidates.items()
        if len(candidates) == 1
    }
    ambiguous_stage_nets = sorted(
        stage_net
        for stage_net, candidates in stage_to_base_candidates.items()
        if len(candidates) > 1
    )
    unassigned_stage_nets = sorted(
        set(stage_net_names) - set(stage_to_base)
    )
    return {
        "schema": "r2g2_base_to_stage_net_lineage_v1",
        "records": records,
        "stage_to_base": stage_to_base,
        "summary": {
            "base_net_count": len(base_net_names),
            "stage_net_count": len(stage_net_names),
            "split_base_net_count": sum(
                int(record["split_flag"]) for record in records.values()
            ),
            "renamed_base_net_count": sum(
                int(record["renamed_flag"]) for record in records.values()
            ),
            "base_nets_with_inferred_backend_stage_nets": sum(
                int(record["inferred_backend_stage_net_count"] > 0)
                for record in records.values()
            ),
            "inferred_backend_stage_net_count": sum(
                int(record["inferred_backend_stage_net_count"])
                for record in records.values()
            ),
            "ambiguous_stage_net_count": len(ambiguous_stage_nets),
            "unaligned_base_net_count": sum(
                int(not record["stage_nets"])
                for record in records.values()
            ),
            "unassigned_stage_net_count": len(unassigned_stage_nets),
            "transparent_added_component_count": len(
                transparent_added_components
            ),
            "ignored_nontransparent_added_component_count": len(
                ignored_nontransparent_added_components
            ),
        },
        "ambiguous_stage_nets": ambiguous_stage_nets,
        "unassigned_stage_nets": unassigned_stage_nets,
    }


def canonical_name(value: str) -> str:
    return (value or "").replace("\\", "").strip()


LIBERTY_GLOBS = ("*.lib", "*.lib.gz")


def read_liberty_text(path: Path) -> str:
    """Read a Liberty file, transparently decompressing ``.lib.gz``.

    r2g-skills delta vs upstream R2G2.0 (D8): upstream used
    ``path.read_text()`` unconditionally. gf180 ships **only** gzipped Liberty
    (30 ``.lib.gz``, 0 ``.lib``), and ORFS ``LIB_FILES`` points straight at one,
    so upstream decoded gzip bytes as UTF-8-with-replacement: zero cells parsed,
    zero pin directions, and **no exception**. Every Liberty feature silently
    died and ``project_gate_graph`` emitted no gate->gate edges at all, while the
    run still reported success. Yosys itself reads the ``.gz`` fine, so only the
    Python side was blind. Matches ``techlib/liberty.py``, which already
    decompresses transparently.
    """

    if str(path).endswith(".gz"):
        import gzip

        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8", errors="replace")


def glob_liberty(directory: Path) -> list[Path]:
    """All Liberty in a directory, compressed or not (sorted, deduped)."""

    found: list[Path] = []
    for pattern in LIBERTY_GLOBS:
        found.extend(sorted(directory.glob(pattern)))
    return list(dict.fromkeys(found))


def load_config(path: str) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        cfg = json.load(handle)
    return config_path, cfg


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


def load_encode_maps(
    config_path: Path, cfg: dict[str, Any]
) -> tuple[Path, dict[str, dict[str, int]]]:
    """读取与阶段1相同的统一编码表，所有_id都必须从这里查表。"""

    encode_path = resolve_path(
        config_path, str(cfg.get("encode_map", "encode_map.csv")), True
    )
    assert encode_path is not None
    with encode_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    platform = str(cfg.get("platform", "")).strip().lower()
    maps: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        technology = row.get("technology", "").strip().lower()
        if technology not in {"global", "*", platform}:
            continue
        map_name = row.get("map_name", "").strip()
        raw_value = row.get("raw_value", "").strip().upper()
        try:
            encoded_id = int(row.get("encoded_id", ""))
        except ValueError as error:
            raise ValueError(f"encode_map encoded_id不是整数: {row}") from error
        previous = maps[map_name].get(raw_value)
        if previous is not None and previous != encoded_id:
            raise ValueError(
                f"encode_map存在冲突: {map_name}/{raw_value}={previous},{encoded_id}"
            )
        maps[map_name][raw_value] = encoded_id
    required_maps = {
        "cell_type_id",
        "cell_function_id",
        "clock_domain_id",
        "orientation_id",
        "placement_status_id",
        "pin_direction_id",
        "pin_role_id",
        "pin_type_id",
        "net_type_id",
        "pin_layer_id",
    }
    missing = required_maps - set(maps)
    if missing:
        raise ValueError(f"encode_map缺少映射组: {sorted(missing)}")
    return encode_path, dict(maps)


def encode_id(
    maps: dict[str, dict[str, int]], map_name: str, raw_value: str
) -> int:
    mapping = maps[map_name]
    key = (raw_value or "UNKNOWN").strip().upper() or "UNKNOWN"
    if key in mapping:
        return mapping[key]
    if "UNKNOWN" in mapping:
        return mapping["UNKNOWN"]
    raise ValueError(f"{map_name}没有编码 {key!r}，且未定义UNKNOWN")


def validate_manifest_stage(
    config_path: Path,
    cfg: dict[str, Any],
    artifact_name: str,
    actual_path: Path,
    semantic_token: str,
) -> dict[str, str]:
    """用raw manifest确认DEF路径和阶段语义，避免把floorplan/route误接为placement。"""

    raw = str(cfg.get("raw_manifest", ""))
    if not raw:
        return {"manifest": "", "semantics": ""}
    manifest_path = resolve_path(config_path, raw, True)
    assert manifest_path is not None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = manifest.get("artifacts", {}).get(artifact_name)
    if not artifact:
        raise ValueError(f"manifest 缺少 artifact={artifact_name}")
    expected_path = (manifest_path.parent / artifact["path"]).resolve()
    if actual_path != expected_path:
        raise ValueError(
            f"{artifact_name} 路径与manifest不一致: "
            f"actual={actual_path}, manifest={expected_path}"
        )
    semantics = str(artifact.get("semantics", "")).lower()
    if semantic_token not in semantics:
        raise ValueError(
            f"{artifact_name} semantics={semantics!r} 不包含 {semantic_token!r}"
        )
    return {"manifest": str(manifest_path), "semantics": semantics}


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


def parse_def(path: Path) -> dict[str, Any]:
    """统一解析后续特征所需的 DEF 子集。"""

    dbu = 1.0
    die = (0, 0, 0, 0)
    track_counts: list[int] = []
    gcell_x = 0
    gcell_y = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            unit_match = re.match(r"UNITS DISTANCE MICRONS\s+(\d+)", line)
            if unit_match:
                dbu = float(unit_match.group(1))
            die_match = re.match(
                r"DIEAREA\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+"
                r"\(\s*(-?\d+)\s+(-?\d+)\s*\)",
                line,
            )
            if die_match:
                die = tuple(map(int, die_match.groups()))
            if line.startswith("TRACKS"):
                count_match = re.search(r"\bDO\s+(\d+)", line)
                if count_match:
                    track_counts.append(int(count_match.group(1)))
            if line.startswith("GCELLGRID X"):
                match = re.search(r"\bSTEP\s+(\d+)", line)
                if match:
                    gcell_x = int(match.group(1))
            if line.startswith("GCELLGRID Y"):
                match = re.search(r"\bSTEP\s+(\d+)", line)
                if match:
                    gcell_y = int(match.group(1))
            if line.startswith("COMPONENTS"):
                break

    components: dict[str, dict[str, Any]] = {}
    for entry in iter_def_entries(path, "COMPONENTS"):
        parts = entry.split()
        if len(parts) < 3:
            continue
        instance = canonical_name(parts[1])
        master = canonical_name(parts[2])
        place_match = re.search(
            r"\+\s*(PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)"
            r"(?:\s+(N|S|E|W|FN|FS|FE|FW))?",
            entry,
        )
        status = "UNPLACED" if "+ UNPLACED" in entry else ""
        x = y = None
        orient = ""
        if place_match:
            status = place_match.group(1)
            x = int(place_match.group(2))
            y = int(place_match.group(3))
            orient = place_match.group(4) or "N"
        components[instance] = {
            "master": master,
            "status": status,
            "x": x,
            "y": y,
            "orient": orient,
        }

    iopins: dict[str, dict[str, Any]] = {}
    for entry in iter_def_entries(path, "PINS"):
        name_match = re.match(r"-\s+(\S+)", entry)
        if not name_match:
            continue
        name = canonical_name(name_match.group(1))
        net_match = re.search(r"\+\s*NET\s+(\S+)", entry)
        direction_match = re.search(r"\+\s*DIRECTION\s+(\S+)", entry)
        use_match = re.search(r"\+\s*USE\s+(\S+)", entry)
        layer_match = re.search(r"\+\s*LAYER\s+(\S+)", entry)
        place_match = re.search(
            r"\+\s*(?:PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", entry
        )
        iopins[name] = {
            "net": canonical_name(net_match.group(1)) if net_match else "",
            "direction": canonical_name(direction_match.group(1)).upper()
            if direction_match
            else "",
            "use": canonical_name(use_match.group(1)).upper()
            if use_match
            else "SIGNAL",
            "layer": canonical_name(layer_match.group(1)) if layer_match else "",
            "x": int(place_match.group(1)) if place_match else None,
            "y": int(place_match.group(2)) if place_match else None,
        }

    nets: dict[str, dict[str, Any]] = {}
    for entry in iter_def_entries(path, "NETS"):
        name_match = re.match(r"-\s+(\S+)", entry)
        if not name_match:
            continue
        name = canonical_name(name_match.group(1))
        connection_text = entry.split("+", 1)[0]
        connections: list[tuple[str, str]] = []
        for left, right in re.findall(
            r"\(\s*([^\s()]+)\s+([^\s()]+)\s*\)", connection_text
        ):
            connections.append((canonical_name(left), canonical_name(right)))
        use_match = re.search(r"\+\s*USE\s+(\S+)", entry)
        layers = {
            canonical_name(layer)
            for layer in re.findall(r"(?:ROUTED|NEW|FIXED)\s+(\S+)", entry)
            if not re.fullmatch(r"-?\d+", layer)
        }
        nets[name] = {
            "connections": list(dict.fromkeys(connections)),
            "use": canonical_name(use_match.group(1)).upper()
            if use_match
            else "",
            "layers": layers,
        }
    return {
        "dbu": dbu,
        "die": die,
        "tracks": track_counts,
        "gcell_x": gcell_x,
        "gcell_y": gcell_y,
        "components": components,
        "iopins": iopins,
        "nets": nets,
    }


def capacitance_scale_to_ff(magnitude: float, unit: str) -> float:
    return magnitude * {
        "ff": 1.0,
        "pf": 1e3,
        "nf": 1e6,
        "uf": 1e9,
    }.get(unit.lower(), 1.0)


def liberty_bus_members(text: str) -> dict[str, list[int]]:
    """解析Liberty type块，将宏单元总线展开到正式bit下标。"""

    members: dict[str, list[int]] = {}
    for match in re.finditer(
        r"type\s*\(\s*\"?([^)\"]+?)\"?\s*\)\s*\{(.*?)\}",
        text,
        flags=re.DOTALL,
    ):
        name = canonical_name(match.group(1))
        body = match.group(2)
        width_match = re.search(r"\bbit_width\s*:\s*\"?(-?\d+)\"?", body)
        from_match = re.search(r"\bbit_from\s*:\s*\"?(-?\d+)\"?", body)
        to_match = re.search(r"\bbit_to\s*:\s*\"?(-?\d+)\"?", body)
        if from_match and to_match:
            start = int(from_match.group(1))
            stop = int(to_match.group(1))
            step = 1 if stop >= start else -1
            values = list(range(start, stop + step, step))
        elif width_match:
            values = list(range(int(width_match.group(1))))
        else:
            continue
        if width_match and len(values) != int(width_match.group(1)):
            raise ValueError(
                f"Liberty总线类型{name}的bit_width与bit_from/bit_to不一致"
            )
        members[name] = values
    return members


def parse_liberty(paths: list[Path]) -> dict[str, Any]:
    """解析Cell/Pin静态属性。

    本阶段只把Liberty固有属性作为输入特征，不读取OpenSTA计算得到的arrival、
    slew或delay。顺序单元类型、Pin电容和约束等信息与后端标签无关，可安全用于
    Placement阶段的通用图特征。
    """

    db: dict[str, Any] = {"cells": {}, "v_nom": None, "cap_scale_ff": 1.0}
    for path in paths:
        text = read_liberty_text(path)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        bus_members = liberty_bus_members(text)
        depth = 0
        current_cell = ""
        current_pin = ""
        current_port_kind = ""
        current_bus_type = ""
        cell_depth = pin_depth = -1
        for raw in text.splitlines():
            line = raw.strip()
            cap_unit = re.search(
                r"capacitive_load_unit\s*\(\s*\"?([0-9eE+.\-]+)\"?\s*,\s*\"?([A-Za-z]+)\"?",
                line,
            )
            if cap_unit:
                db["cap_scale_ff"] = capacitance_scale_to_ff(
                    float(cap_unit.group(1)), cap_unit.group(2)
                )
            if db["v_nom"] is None:
                voltage = re.search(r"\bnom_voltage\s*:\s*\"?([0-9eE+.\-]+)\"?", line)
                if voltage:
                    db["v_nom"] = float(voltage.group(1))
            opens = line.count("{")
            closes = line.count("}")
            cell_match = re.match(r"cell\s*\(\s*\"?([^)\"]+?)\"?\s*\)\s*\{", line)
            if cell_match:
                current_cell = canonical_name(cell_match.group(1)).upper()
                db["cells"].setdefault(
                    current_cell,
                    {
                        "area": 0.0,
                        "power": 0.0,
                        "pins": {},
                        "is_ff": False,
                        "is_latch": False,
                    },
                )
                cell_depth = depth + opens
                current_pin = ""
            pin_match = re.match(
                r"(pin|bus)\s*\(\s*\"?([^)\"]+?)\"?\s*\)\s*\{", line
            )
            if current_cell and pin_match:
                current_port_kind = pin_match.group(1)
                current_pin = canonical_name(pin_match.group(2))
                current_bus_type = ""
                db["cells"][current_cell]["pins"].setdefault(
                    current_pin,
                    {
                        "direction": "",
                        "cap_fF": 0.0,
                        "clock": False,
                        "max_transition_ns": 0.0,
                        "max_capacitance_fF": 0.0,
                        "function": "",
                    },
                )
                pin_depth = depth + opens
            bus_type = re.match(r"bus_type\s*:\s*\"?([^;\"]+?)\"?\s*;", line)
            if current_cell and current_pin and bus_type:
                current_bus_type = canonical_name(bus_type.group(1))
            if current_cell and not current_pin:
                if re.match(r"(?:ff|ff_bank)\s*\(", line):
                    db["cells"][current_cell]["is_ff"] = True
                if re.match(r"(?:latch|latch_bank)\s*\(", line):
                    db["cells"][current_cell]["is_latch"] = True
                area = re.match(r"area\s*:\s*\"?([0-9eE+.\-]+)\"?", line)
                power = re.match(
                    r"cell_leakage_power\s*:\s*\"?([0-9eE+.\-]+)\"?", line
                )
                if area:
                    db["cells"][current_cell]["area"] = float(area.group(1))
                if power:
                    db["cells"][current_cell]["power"] = float(power.group(1))
            if current_cell and current_pin:
                direction = re.match(r"direction\s*:\s*\"?([A-Za-z_]+)\"?", line)
                cap = re.match(r"capacitance\s*:\s*\"?([0-9eE+.\-]+)\"?", line)
                max_transition = re.match(
                    r"max_transition\s*:\s*\"?([0-9eE+.\-]+)\"?", line
                )
                max_capacitance = re.match(
                    r"max_capacitance\s*:\s*\"?([0-9eE+.\-]+)\"?", line
                )
                function = re.match(r'function\s*:\s*"([^"]*)"', line)
                if direction:
                    db["cells"][current_cell]["pins"][current_pin][
                        "direction"
                    ] = direction.group(1).upper()
                if cap:
                    db["cells"][current_cell]["pins"][current_pin]["cap_fF"] = (
                        float(cap.group(1)) * db["cap_scale_ff"]
                    )
                if max_transition:
                    db["cells"][current_cell]["pins"][current_pin][
                        "max_transition_ns"
                    ] = float(max_transition.group(1))
                if max_capacitance:
                    db["cells"][current_cell]["pins"][current_pin][
                        "max_capacitance_fF"
                    ] = float(max_capacitance.group(1)) * db["cap_scale_ff"]
                if function:
                    db["cells"][current_cell]["pins"][current_pin][
                        "function"
                    ] = function.group(1)
                if re.match(
                    r"clock\s*:\s*\"?true\"?", line, flags=re.IGNORECASE
                ):
                    db["cells"][current_cell]["pins"][current_pin]["clock"] = True
            depth += opens - closes
            if current_pin and depth < pin_depth:
                if current_port_kind == "bus":
                    indices = bus_members.get(current_bus_type)
                    if not indices:
                        raise ValueError(
                            f"{path}: bus({current_pin})引用未知类型"
                            f"{current_bus_type!r}"
                        )
                    properties = dict(
                        db["cells"][current_cell]["pins"][current_pin]
                    )
                    for index in indices:
                        bit_name = f"{current_pin}[{index}]"
                        previous = db["cells"][current_cell]["pins"].get(
                            bit_name
                        )
                        if previous is not None and previous != properties:
                            raise ValueError(
                                f"Liberty Bus bit属性冲突: "
                                f"{current_cell}/{bit_name}"
                            )
                        db["cells"][current_cell]["pins"][bit_name] = dict(
                            properties
                        )
                current_pin = ""
                current_port_kind = ""
                current_bus_type = ""
            if current_cell and depth < cell_depth:
                current_cell = ""
                current_pin = ""
    return db


def parse_lef_geometry(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """解析LEF宏类别、尺寸与Pin几何，用于坐标可信性和绝对Pin位置。"""

    macros: dict[str, dict[str, Any]] = {}
    for path in paths:
        current_macro = ""
        current_pin = ""
        rectangles: list[tuple[float, float, float, float]] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                macro_match = re.match(r"MACRO\s+(\S+)", line)
                if macro_match:
                    current_macro = canonical_name(macro_match.group(1)).upper()
                    macros.setdefault(
                        current_macro,
                        {
                            "class": "",
                            "width": 0.0,
                            "height": 0.0,
                            "pins": {},
                        },
                    )
                    continue
                if not current_macro:
                    continue
                macro_class = re.match(r"CLASS\s+(\S+)", line)
                if macro_class and not current_pin:
                    macros[current_macro]["class"] = canonical_name(
                        macro_class.group(1)
                    ).upper()
                size = re.search(
                    r"SIZE\s+([0-9eE+.\-]+)\s+BY\s+([0-9eE+.\-]+)", line
                )
                if size:
                    macros[current_macro]["width"] = float(size.group(1))
                    macros[current_macro]["height"] = float(size.group(2))
                pin_match = re.match(r"PIN\s+(\S+)", line)
                if pin_match:
                    current_pin = canonical_name(pin_match.group(1))
                    rectangles = []
                    continue
                rect = re.search(
                    r"RECT\s+([0-9eE+.\-]+)\s+([0-9eE+.\-]+)\s+"
                    r"([0-9eE+.\-]+)\s+([0-9eE+.\-]+)",
                    line,
                )
                if current_pin and rect:
                    rectangles.append(tuple(map(float, rect.groups())))
                # r2g-skills delta vs upstream R2G2.0 (D10): upstream read pin
                # geometry from RECT only. LEF allows POLYGON, and gf180's
                # standard cells use it for essentially every signal pin (2135
                # POLYGON vs 1294 RECT in the 9t SC LEF). With no geometry the
                # pin falls back to the cell origin, pin_position_valid goes 0
                # for EVERY pin, and because stage_net_geometry requires every
                # endpoint to have geometry, EVERY net's HPWL/bbox becomes NaN.
                # Observed exactly that on gf180: 13 columns all-NaN at cts and
                # route. Matches techlib/lef.py, which already reads both.
                polygon = re.search(r"\bPOLYGON\s+([-0-9eE.\s]+)", line)
                if current_pin and polygon:
                    values = [float(v) for v in polygon.group(1).split()]
                    if len(values) >= 6 and len(values) % 2 == 0:
                        xs_p = values[0::2]
                        ys_p = values[1::2]
                        # Store the polygon's bbox as one rectangle so the
                        # centroid rule below is identical for both shapes.
                        rectangles.append((min(xs_p), min(ys_p), max(xs_p), max(ys_p)))
                end_match = re.match(r"END\s+(\S+)", line)
                if end_match and current_pin and canonical_name(
                    end_match.group(1)
                ) == current_pin:
                    if rectangles:
                        xs = [v for rect_row in rectangles for v in (rect_row[0], rect_row[2])]
                        ys = [v for rect_row in rectangles for v in (rect_row[1], rect_row[3])]
                        macros[current_macro]["pins"][current_pin] = (
                            (min(xs) + max(xs)) / 2.0,
                            (min(ys) + max(ys)) / 2.0,
                        )
                    current_pin = ""
                    rectangles = []
                elif end_match and canonical_name(end_match.group(1)).upper() == current_macro:
                    current_macro = ""
                    current_pin = ""
    return macros


def apply_coordinate_trust_policy(
    snapshot: dict[str, Any],
    prediction_stage: str,
    lef: dict[str, dict[str, Any]],
    base_gate_names: set[str],
) -> dict[str, Any]:
    """屏蔽预测时点尚不存在的标准单元坐标。

    post-floorplan DEF可能把未放置标准单元写成大量重合的``PLACED``临时坐标。
    Placement预测只保留FIXED实例和LEF宏类实例；post-placement/post-CTS中的
    标准单元位置才可用于坐标特征、HPWL、密度和Gate几何边。
    """

    raw_positioned = trusted_positioned = filtered_placeholder = 0
    raw_base_positioned = trusted_base_positioned = 0
    trusted_fixed = trusted_macro = 0
    for name, component in snapshot["components"].items():
        positioned = component.get("x") is not None and component.get("y") is not None
        if not positioned:
            continue
        raw_positioned += 1
        raw_base_positioned += int(name in base_gate_names)
        master = str(component.get("master", "")).upper()
        macro_class = str(lef.get(master, {}).get("class", "")).upper()
        fixed = str(component.get("status", "")).upper() == "FIXED"
        macro = macro_class in TRUSTED_FLOORPLAN_MACRO_CLASSES
        trusted = (
            prediction_stage in TRUSTED_STANDARD_CELL_POSITION_STAGES
            or (prediction_stage == "placement" and (fixed or macro))
        )
        if trusted:
            trusted_positioned += 1
            trusted_base_positioned += int(name in base_gate_names)
            trusted_fixed += int(fixed)
            trusted_macro += int(macro)
            continue
        component["x"] = None
        component["y"] = None
        component["orient"] = ""
        filtered_placeholder += 1

    return {
        "policy": (
            "post_yosys_no_coordinates;post_floorplan_fixed_macro_io_only;"
            "post_placement_and_post_cts_standard_cell_coordinates_trusted"
        ),
        "standard_cell_coordinates_trusted": int(
            prediction_stage in TRUSTED_STANDARD_CELL_POSITION_STAGES
        ),
        "gate_geom_edge_eligible": int(
            prediction_stage in TRUSTED_STANDARD_CELL_POSITION_STAGES
        ),
        "hpwl_feature_eligible": int(
            prediction_stage in TRUSTED_STANDARD_CELL_POSITION_STAGES
        ),
        "congestion_feature_eligible": int(
            prediction_stage in TRUSTED_STANDARD_CELL_POSITION_STAGES
        ),
        "raw_positioned_component_count": raw_positioned,
        "trusted_positioned_component_count": trusted_positioned,
        "filtered_placeholder_component_count": filtered_placeholder,
        "raw_positioned_base_gate_count": raw_base_positioned,
        "trusted_positioned_base_gate_count": trusted_base_positioned,
        "trusted_fixed_component_count": trusted_fixed,
        "trusted_macro_component_count": trusted_macro,
    }


def transform_pin(
    x: float, y: float, orient: str, width: float, height: float
) -> tuple[float, float]:
    """把LEF cell内Pin坐标按DEF orientation变换到以(0,0)为原点的放置坐标系。

    r2g-skills delta vs upstream R2G2.0: upstream的``FN``/``FS``两项互换了
    (``FN``写成``(x, h-y)``=MX, ``FS``写成``(w-x, y)``=MY)。LEF/DEF规定
    ``FN``=MY(沿Y轴镜像, 改x), ``FS``=MX(沿X轴镜像, 改y)。标准单元行交替
    使用N/FS, 因此该互换会让接近一半实例的Pin坐标、HPWL、pin density和RUDY
    全部错位。经OpenDB实测(aes_core/sky130hd, 401个已布放Pin): 上游公式在
    FS实例上0/190命中, 修正后190/190命中, N实例两者都是211/211。
    与``def-graph/scripts/extract/techlib/lef.py:apply_orient``保持一致。
    """

    orientation = (orient or "N").upper()
    transforms = {
        "N": (x, y),
        "S": (width - x, height - y),
        "W": (height - y, x),
        "E": (y, width - x),
        "FN": (width - x, y),
        "FS": (x, height - y),
        "FW": (y, x),
        "FE": (height - y, width - x),
    }
    return transforms.get(orientation, (x, y))


def absolute_pin_position(
    component: dict[str, Any],
    pin_name: str,
    dbu: float,
    lef: dict[str, dict[str, Any]],
) -> tuple[float, float]:
    origin_x = float(component.get("x") or 0) / dbu
    origin_y = float(component.get("y") or 0) / dbu
    macro = lef.get(str(component.get("master", "")).upper())
    if not macro or pin_name not in macro["pins"]:
        return origin_x, origin_y
    dx, dy = transform_pin(
        *macro["pins"][pin_name],
        str(component.get("orient", "N")),
        float(macro["width"]),
        float(macro["height"]),
    )
    return origin_x + dx, origin_y + dy


def pin_position_features(
    component: dict[str, Any] | None,
    pin_name: str,
    dbu: float,
    lef: dict[str, dict[str, Any]],
    die_um: tuple[float, float, float, float],
) -> dict[str, float | int]:
    """由当前阶段组件位置和LEF Pin几何计算绝对/归一化坐标。"""

    left, bottom, right, top = die_um
    width = max(right - left, 1e-12)
    height = max(top - bottom, 1e-12)
    if (
        component is None
        or component.get("x") is None
        or component.get("y") is None
    ):
        x = y = float("nan")
        valid = 0
    else:
        x, y = absolute_pin_position(component, pin_name, dbu, lef)
        macro = lef.get(str(component.get("master", "")).upper(), {})
        valid = int(pin_name in macro.get("pins", {}))
    return {
        "pin_x_um": x,
        "pin_y_um": y,
        "pin_x_normalized": (x - left) / width if valid else float("nan"),
        "pin_y_normalized": (y - bottom) / height if valid else float("nan"),
        "distance_to_die_left_um": max(0.0, x - left)
        if valid
        else float("nan"),
        "distance_to_die_right_um": max(0.0, right - x)
        if valid
        else float("nan"),
        "distance_to_die_bottom_um": max(0.0, y - bottom)
        if valid
        else float("nan"),
        "distance_to_die_top_um": max(0.0, top - y)
        if valid
        else float("nan"),
        "pin_position_valid": valid,
    }


def point_position_features(
    x: float,
    y: float,
    valid: bool,
    die_um: tuple[float, float, float, float],
) -> dict[str, float | int]:
    left, bottom, right, top = die_um
    width = max(right - left, 1e-12)
    height = max(top - bottom, 1e-12)
    return {
        "pin_x_normalized": (x - left) / width if valid else float("nan"),
        "pin_y_normalized": (y - bottom) / height if valid else float("nan"),
        "distance_to_die_left_um": max(0.0, x - left)
        if valid
        else float("nan"),
        "distance_to_die_right_um": max(0.0, right - x)
        if valid
        else float("nan"),
        "distance_to_die_bottom_um": max(0.0, y - bottom)
        if valid
        else float("nan"),
        "distance_to_die_top_um": max(0.0, top - y)
        if valid
        else float("nan"),
        "pin_position_valid": int(valid),
    }


def oriented_size(
    width: float, height: float, orientation: str
) -> tuple[float, float]:
    """返回DEF方向下的轴对齐宽高，供cell中心和网格覆盖计算。"""

    if (orientation or "N").upper() in {"E", "W", "FE", "FW"}:
        return height, width
    return width, height


def grid_keys_for_bbox(
    left: float,
    bottom: float,
    right: float,
    top: float,
    origin_x: float,
    origin_y: float,
    step_x: float,
    step_y: float,
    count_x: int,
    count_y: int,
) -> list[tuple[int, int]]:
    """列出与闭开矩形 ``[left,right)×[bottom,top)`` 相交的有效GCell。"""

    if step_x <= 0 or step_y <= 0 or count_x <= 0 or count_y <= 0:
        return []
    left, right = sorted((left, right))
    bottom, top = sorted((bottom, top))
    epsilon = 1e-12
    gx0 = math.floor((left - origin_x) / step_x)
    gy0 = math.floor((bottom - origin_y) / step_y)
    gx1 = math.floor(((right - epsilon) - origin_x) / step_x) if right > left else gx0
    gy1 = math.floor(((top - epsilon) - origin_y) / step_y) if top > bottom else gy0
    gx0, gx1 = max(0, gx0), min(count_x - 1, gx1)
    gy0, gy1 = max(0, gy0), min(count_y - 1, gy1)
    if gx0 > gx1 or gy0 > gy1:
        return []
    return [
        (grid_x, grid_y)
        for grid_x in range(gx0, gx1 + 1)
        for grid_y in range(gy0, gy1 + 1)
    ]


def compute_congestion_features(
    gate_names: list[str],
    gate_masters: dict[str, str],
    place: dict[str, Any],
    route: dict[str, Any],
    lef: dict[str, dict[str, Any]],
    connections_by_net: dict[str, list[tuple[str, str]]],
    io_by_net: dict[str, list[str]],
    io_only_nets: dict[str, list[str]],
    prediction_stage: str,
    grid_um: float = FIXED_CONGESTION_GRID_UM,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """在技术库预先确定的2.1um网格上复现五类拥塞输入特征。

    与旧脚本相比，Pin使用LEF包围盒中心而不是逐Pin矩形覆盖；其余定义保持一致：
    cell/net/pin density为网格计数，RUDY为Net包围盒的
    ``(1/width + 1/height) × overlap``，最后对Gate覆盖的GCell取均值。
    坐标只来自当前阶段快照；网格尺寸是可在Route前由技术库确定的常量
    ``15 × Metal3 pitch(0.14um) = 2.1um``，不会读取Routing DEF。
    """

    dbu = float(place["dbu"])
    step_x = float(grid_um)
    step_y = float(grid_um)
    die = tuple(float(value) for value in place["die"])
    if dbu <= 0 or die[2] <= die[0] or die[3] <= die[1]:
        nan = float("nan")
        return (
            {
                name: {
                    "congestion_pin_density": nan,
                    "congestion_cell_density": nan,
                    "congestion_net_density": nan,
                    "congestion_rudy": nan,
                    "congestion_rudy_pin": nan,
                    "congestion_feature_valid": 0,
                }
                for name in gate_names
            },
            {
                "grid_step_x_um": nan,
                "grid_step_y_um": nan,
                "grid_count_x": 0,
                "grid_count_y": 0,
                "occupied_cell_grids": 0,
                "occupied_pin_grids": 0,
                "occupied_net_grids": 0,
                "source_stage": "current_snapshot",
                "grid_spec_source_stage": (
                    "technology_constant_15x_metal3_pitch_pre_route"
                ),
                "grid_step_dbu": 0,
            },
        )
    die_left, die_bottom, die_right, die_top = (
        float(value) / dbu for value in place["die"]
    )
    count_x = max(1, math.ceil((die_right - die_left) / step_x))
    count_y = max(1, math.ceil((die_top - die_bottom) / step_y))
    if prediction_stage not in TRUSTED_STANDARD_CELL_POSITION_STAGES:
        nan = float("nan")
        return (
            {
                name: {
                    "congestion_pin_density": nan,
                    "congestion_cell_density": nan,
                    "congestion_net_density": nan,
                    "congestion_rudy": nan,
                    "congestion_rudy_pin": nan,
                    "congestion_feature_valid": 0,
                }
                for name in gate_names
            },
            {
                "grid_step_x_um": step_x,
                "grid_step_y_um": step_y,
                "grid_count_x": count_x,
                "grid_count_y": count_y,
                "occupied_cell_grids": 0,
                "occupied_pin_grids": 0,
                "occupied_net_grids": 0,
                "source_stage": "unavailable_before_standard_cell_placement",
                "grid_spec_source_stage": (
                    "technology_constant_15x_metal3_pitch_pre_route"
                ),
                "grid_step_dbu": int(round(step_x * dbu)),
            },
        )

    cell_density: dict[tuple[int, int], float] = defaultdict(float)
    pin_density: dict[tuple[int, int], float] = defaultdict(float)
    net_density: dict[tuple[int, int], float] = defaultdict(float)
    rudy: dict[tuple[int, int], float] = defaultdict(float)
    rudy_pin: dict[tuple[int, int], float] = defaultdict(float)
    gate_grids: dict[str, list[tuple[int, int]]] = {}

    gate_name_set = set(gate_names)
    for name, component in place["components"].items():
        if component.get("x") is None or component.get("y") is None:
            continue
        macro = lef.get(str(component.get("master", "")).upper(), {})
        width, height = oriented_size(
            float(macro.get("width", 0.0)),
            float(macro.get("height", 0.0)),
            str(component.get("orient", "N")),
        )
        x = float(component["x"]) / dbu
        y = float(component["y"]) / dbu
        keys = grid_keys_for_bbox(
            x,
            y,
            x + width,
            y + height,
            die_left,
            die_bottom,
            step_x,
            step_y,
            count_x,
            count_y,
        )
        if name in gate_name_set:
            gate_grids[name] = keys
        for key in keys:
            cell_density[key] += 1.0
    for name in gate_names:
        gate_grids.setdefault(name, [])

    # 密度/RUDY必须使用当前物理快照的小网拓扑；否则fakeram拆成数百个小网后仍会
    # 被当作跨芯片大bbox。补入PINS段中存在、但NETS连接列表可能漏写的IO-only Net。
    physical_connections_by_net = {
        net_name: list(info.get("connections", []))
        for net_name, info in route["nets"].items()
    }
    for io_name, info in place["iopins"].items():
        net_name = canonical_name(str(info.get("net", "")))
        if not net_name:
            continue
        connections_for_net = physical_connections_by_net.setdefault(
            net_name, []
        )
        if ("PIN", io_name) not in connections_for_net:
            connections_for_net.append(("PIN", io_name))

    for _, physical_pins in physical_connections_by_net.items():
        points: list[tuple[float, float]] = []
        per_net_pin_density: dict[tuple[int, int], float] = defaultdict(float)
        for instance, pin_name in physical_pins:
            if instance == "PIN":
                physical = place["iopins"].get(pin_name, {})
                if physical.get("x") is None or physical.get("y") is None:
                    continue
                point = (
                    float(physical["x"]) / dbu,
                    float(physical["y"]) / dbu,
                )
            else:
                component = place["components"].get(instance)
                if (
                    not component
                    or component.get("x") is None
                    or component.get("y") is None
                ):
                    continue
                point = absolute_pin_position(
                    component, pin_name, dbu, lef
                )
            points.append(point)
            keys = grid_keys_for_bbox(
                point[0],
                point[1],
                point[0],
                point[1],
                die_left,
                die_bottom,
                step_x,
                step_y,
                count_x,
                count_y,
            )
            for key in keys:
                pin_density[key] += 1.0
                per_net_pin_density[key] += 1.0
        if not points:
            continue
        left = min(point[0] for point in points)
        right = max(point[0] for point in points)
        bottom = min(point[1] for point in points)
        top = max(point[1] for point in points)
        keys = grid_keys_for_bbox(
            left,
            bottom,
            right,
            top,
            die_left,
            die_bottom,
            step_x,
            step_y,
            count_x,
            count_y,
        )
        for key in keys:
            net_density[key] += 1.0
        width = right - left
        height = top - bottom
        if width <= 0 or height <= 0:
            continue
        factor = 1.0 / width + 1.0 / height
        for grid_x, grid_y in keys:
            grid_left = die_left + grid_x * step_x
            grid_bottom = die_bottom + grid_y * step_y
            overlap_width = max(
                0.0, min(grid_left + step_x, right) - max(grid_left, left)
            )
            overlap_height = max(
                0.0, min(grid_bottom + step_y, top) - max(grid_bottom, bottom)
            )
            overlap = (overlap_width * overlap_height) / (step_x * step_y)
            rudy[(grid_x, grid_y)] += factor * overlap
            rudy_pin[(grid_x, grid_y)] += factor * per_net_pin_density.get(
                (grid_x, grid_y), 0.0
            )

    values: dict[str, dict[str, float]] = {}
    for name in gate_names:
        keys = gate_grids[name]
        if not keys:
            nan = float("nan")
            values[name] = {
                "congestion_pin_density": nan,
                "congestion_cell_density": nan,
                "congestion_net_density": nan,
                "congestion_rudy": nan,
                "congestion_rudy_pin": nan,
                "congestion_feature_valid": 0,
            }
            continue
        valid = float(bool(keys))
        denominator = float(len(keys))
        values[name] = {
            "congestion_pin_density": sum(pin_density[key] for key in keys)
            / denominator,
            "congestion_cell_density": sum(cell_density[key] for key in keys)
            / denominator,
            "congestion_net_density": sum(net_density[key] for key in keys)
            / denominator,
            "congestion_rudy": sum(rudy[key] for key in keys) / denominator,
            "congestion_rudy_pin": sum(rudy_pin[key] for key in keys)
            / denominator,
            "congestion_feature_valid": valid,
        }
    return values, {
        "grid_step_x_um": step_x,
        "grid_step_y_um": step_y,
        "grid_count_x": count_x,
        "grid_count_y": count_y,
        "occupied_cell_grids": len(cell_density),
        "occupied_pin_grids": len(pin_density),
        "occupied_net_grids": len(net_density),
        "source_stage": "current_snapshot",
        "grid_spec_source_stage": (
            "technology_constant_15x_metal3_pitch_pre_route"
        ),
        "grid_step_dbu": int(round(step_x * dbu)),
    }


def stage_net_geometry(
    net_name: str,
    snapshot: dict[str, Any],
    lef: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """用阶段Net的完整端点（含后端新增cell）计算分段bbox/HPWL。"""

    info = snapshot["nets"].get(net_name)
    if info is None:
        return {
            "net_bbox": (
                float("nan"),
                float("nan"),
                float("nan"),
                float("nan"),
            ),
            "bbox_width_um": float("nan"),
            "bbox_height_um": float("nan"),
            "hpwl_um": float("nan"),
            "valid": 0,
            "endpoint_count": 0,
        }
    dbu = float(snapshot["dbu"])
    points: list[tuple[float, float]] = []
    valid = True
    endpoint_count = 0
    for instance, pin_name in info.get("connections", []):
        endpoint_count += 1
        if instance == "PIN":
            physical = snapshot["iopins"].get(pin_name)
            if (
                physical is None
                or physical.get("x") is None
                or physical.get("y") is None
            ):
                valid = False
                continue
            points.append(
                (
                    float(physical["x"]) / dbu,
                    float(physical["y"]) / dbu,
                )
            )
            continue
        component = snapshot["components"].get(instance)
        if (
            component is None
            or component.get("x") is None
            or component.get("y") is None
        ):
            valid = False
            continue
        macro = lef.get(str(component.get("master", "")).upper(), {})
        if pin_name not in macro.get("pins", {}):
            valid = False
            continue
        points.append(
            absolute_pin_position(component, pin_name, dbu, lef)
        )
    valid = bool(valid and points and len(points) == endpoint_count)
    if not valid:
        return {
            "net_bbox": (
                float("nan"),
                float("nan"),
                float("nan"),
                float("nan"),
            ),
            "bbox_width_um": float("nan"),
            "bbox_height_um": float("nan"),
            "hpwl_um": float("nan"),
            "valid": 0,
            "endpoint_count": endpoint_count,
        }
    width = max(x for x, _ in points) - min(x for x, _ in points)
    height = max(y for _, y in points) - min(y for _, y in points)
    return {
        "net_bbox": (
            min(x for x, _ in points),
            min(y for _, y in points),
            max(x for x, _ in points),
            max(y for _, y in points),
        ),
        "bbox_width_um": width,
        "bbox_height_um": height,
        "hpwl_um": width + height,
        "valid": 1,
        "endpoint_count": endpoint_count,
    }


def direction_id(
    direction: str, maps: dict[str, dict[str, int]]
) -> int:
    return encode_id(maps, "pin_direction_id", direction)


def pin_type_name(master: str, pin: str, lib: dict[str, Any]) -> str:
    upper = pin.upper()
    info = lib["cells"].get(master.upper(), {}).get("pins", {}).get(pin, {})
    direction = str(info.get("direction", "")).upper()
    if upper in POWER_PINS:
        return "POWER"
    if upper in GROUND_PINS:
        return "GROUND"
    if direction in {"INOUT", "FEEDTHRU"}:
        return "INOUT_FEEDTHRU"
    # 先判定输出方向，避免把加法器的S输出误判成SELECT、把SO输出误判成SCAN输入。
    if direction == "OUTPUT":
        return "OUTPUT"
    if info.get("clock") or "CLK" in upper or upper in {"CK", "CP", "CLOCK"}:
        return "CLOCK"
    if "RESET" in upper or upper.startswith("RST") or upper in {"RN", "RSTN"}:
        return "RESET"
    if upper.startswith("SET") or upper in {"SN", "SETN"}:
        return "SET"
    if upper in {"E", "EN", "OE", "TE", "GATE"}:
        return "ENABLE"
    if upper.startswith("SCAN") or upper in {"SE", "SI"}:
        return "SCAN"
    if upper in {"S", "S0", "S1", "SEL", "SELECT"}:
        return "SELECT"
    if direction == "INPUT":
        return {
            "A": "INPUT_A",
            "B": "INPUT_B",
            "C": "INPUT_C",
        }.get(upper[:1], "INPUT_OTHER")
    return "UNKNOWN"


def cell_function_name(master: str, lib_cell: dict[str, Any]) -> str:
    """把工艺相关Master归并为跨设计稳定的功能类别。"""

    upper = master.upper()
    if any(token in upper for token in ("FILL", "TAP", "DECAP", "ENDCAP", "ANTENNA")):
        return "PHYSICAL_ONLY"
    if bool(lib_cell.get("is_latch")):
        return "SEQUENTIAL_LATCH"
    if bool(lib_cell.get("is_ff")):
        return "SEQUENTIAL_FF"
    if "CLKGATE" in upper:
        return "CLOCK_GATE"
    if "CLKBUF" in upper:
        return "CLOCK_BUFFER"
    if upper.startswith(("TBUF", "TINV")) or "TRISTATE" in upper:
        return "TRISTATE"
    if upper.startswith("BUF"):
        return "BUFFER"
    if upper.startswith("INV"):
        return "INVERTER"
    if upper.startswith(("LOGIC0", "LOGIC1", "TIEHI", "TIELO")):
        return "CONSTANT"
    if lib_cell:
        return "COMBINATIONAL_LOGIC"
    return "UNKNOWN"


def drive_strength(master: str) -> float:
    """从标准单元Master后缀提取驱动强度；无法解析时返回0。"""

    match = re.search(r"(?:^|_)X([0-9]+(?:\.[0-9]+)?)$", master.upper())
    return float(match.group(1)) if match else 0.0


def pin_role_name(
    pin_type: str,
    direction: str,
    cell_function: str,
) -> str:
    """将细粒度pin_type归并为时序/逻辑通用Pin角色。"""

    ptype = pin_type.upper()
    direction = direction.upper()
    if ptype == "POWER":
        return "POWER"
    if ptype == "GROUND":
        return "GROUND"
    if ptype == "CLOCK":
        return "CLOCK"
    if ptype == "RESET":
        return "RESET"
    if ptype == "SET":
        return "SET"
    if ptype == "ENABLE":
        return "ENABLE"
    if ptype == "SCAN":
        return "SCAN"
    if ptype == "SELECT":
        return "SELECT"
    if direction in {"INOUT", "FEEDTHRU"}:
        return "INOUT"
    sequential = cell_function in {"SEQUENTIAL_FF", "SEQUENTIAL_LATCH"}
    if sequential and direction == "OUTPUT":
        return "Q"
    if sequential and direction == "INPUT":
        return "DATA"
    if direction == "OUTPUT":
        return "COMBINATIONAL_OUTPUT"
    if direction == "INPUT":
        return "COMBINATIONAL_INPUT"
    return "UNKNOWN"


def io_pin_role_name(direction: str, is_clock_port: bool) -> str:
    if is_clock_port:
        return "CLOCK_PORT"
    return {
        "INPUT": "PRIMARY_INPUT",
        "OUTPUT": "PRIMARY_OUTPUT",
        "INOUT": "PRIMARY_INOUT",
        "FEEDTHRU": "PRIMARY_INOUT",
    }.get(direction.upper(), "UNKNOWN")


def net_type_name(name: str, use: str, clock_ports: set[str]) -> str:
    upper_use = use.upper()
    lower = name.lower()
    if upper_use == "POWER":
        return "POWER"
    if upper_use == "GROUND":
        return "GROUND"
    if upper_use == "CLOCK" or name in clock_ports or "clk" in lower:
        return "CLOCK"
    if "reset" in lower or lower.startswith("rst"):
        return "RESET"
    if "scan" in lower or "test" in lower:
        return "SCAN_TEST"
    return "SIGNAL"


def _sdc_objects(line: str, command: str) -> list[str]:
    """提取一行SDC中get_ports/get_clocks的对象，支持花括号与普通Token。"""

    pattern = rf"\[{command}\s+(?:\{{([^}}]*)\}}|([^\]\s]+))\]"
    values: list[str] = []
    for brace_value, token_value in re.findall(pattern, line):
        raw = brace_value or token_value
        values.extend(canonical_name(item) for item in raw.split() if item.strip())
    return values


def parse_sdc(path: Path | None) -> dict[str, Any]:
    """解析通用图所需的时钟和I/O约束。

    旧实现把SDC中所有``get_ports``都当成Clock Port，导致所有I/O delay端口被误标为
    Clock。本实现只读取``create_clock``的根端口，并独立保存I/O delay。
    """

    empty = {
        "source_path": "",
        "clocks": [],
        "clock_ports": set(),
        "input_delays": {},
        "output_delays": {},
        "frequency_hz": 0.0,
    }
    if not path or not path.is_file():
        return empty
    text = path.read_text(encoding="utf-8", errors="replace")
    variables: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        match = re.match(r"set\s+([A-Za-z_][A-Za-z0-9_]*)\s+(\S+)", line)
        if match:
            variables[match.group(1)] = match.group(2).strip("\"'{}")

    def resolve_token(token: str) -> str:
        token = token.strip("\"'{}")
        return variables.get(token[1:], token) if token.startswith("$") else token

    clocks: list[dict[str, Any]] = []
    input_delays: dict[str, dict[str, Any]] = {}
    output_delays: dict[str, dict[str, Any]] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("create_clock"):
            period_match = re.search(r"-period\s+(\S+)", line)
            name_match = re.search(r"-name\s+(\S+)", line)
            ports = _sdc_objects(line, "get_ports")
            try:
                period = (
                    float(resolve_token(period_match.group(1)))
                    if period_match
                    else 0.0
                )
            except ValueError:
                period = 0.0
            name = (
                canonical_name(resolve_token(name_match.group(1)))
                if name_match
                else (ports[0] if ports else f"clock_{len(clocks)}")
            )
            clocks.append(
                {
                    "name": name,
                    "period_ns": period,
                    "root_ports": ports,
                    "uncertainty_ns": 0.0,
                }
            )
            continue

        delay_match = re.match(r"set_(input|output)_delay\s+(\S+)", line)
        if delay_match:
            try:
                delay = float(resolve_token(delay_match.group(2)))
            except ValueError:
                continue
            clock_names = _sdc_objects(line, "get_clocks")
            target = input_delays if delay_match.group(1) == "input" else output_delays
            for port in _sdc_objects(line, "get_ports"):
                target[port] = {
                    "delay_ns": delay,
                    "clock_name": clock_names[0] if clock_names else "",
                }
            continue

        uncertainty_match = re.match(r"set_clock_uncertainty\s+(\S+)", line)
        if uncertainty_match:
            try:
                uncertainty = float(resolve_token(uncertainty_match.group(1)))
            except ValueError:
                continue
            names = set(_sdc_objects(line, "get_clocks"))
            for clock in clocks:
                if not names or clock["name"] in names:
                    clock["uncertainty_ns"] = uncertainty

    clocks.sort(key=lambda row: (row["name"], row["period_ns"]))
    for index, clock in enumerate(clocks):
        clock["domain_raw"] = f"DOMAIN_{index}"
    clock_ports = {
        port for clock in clocks for port in clock.get("root_ports", [])
    }
    periods = [
        float(clock["period_ns"])
        for clock in clocks
        if float(clock["period_ns"]) > 0
    ]
    return {
        "source_path": str(path),
        "clocks": clocks,
        "clock_ports": clock_ports,
        "input_delays": input_delays,
        "output_delays": output_delays,
        "frequency_hz": 1e9 / min(periods) if periods else 0.0,
    }


def _longest_dag_levels(
    vertices: set[tuple[str, ...]],
    adjacency: dict[tuple[str, ...], set[tuple[str, ...]]],
    seeds: set[tuple[str, ...]],
    reverse: bool = False,
) -> tuple[dict[tuple[str, ...], int], set[tuple[str, ...]]]:
    """只从显式时序端点传播最长层级，并返回可验证DAG节点集合。

    前向调用的 ``seeds`` 必须是结构性 startpoint，反向调用的 ``seeds``
    必须是结构性 endpoint。不能把任意零入度/零出度节点自动补成种子，否则
    悬空Pin、无负载输出和未约束的独立逻辑分量也会被错误标记为有效时序层级。
    """

    graph: dict[tuple[str, ...], set[tuple[str, ...]]] = {
        vertex: set(adjacency.get(vertex, set())) for vertex in vertices
    }
    if reverse:
        reversed_graph = {vertex: set() for vertex in vertices}
        for source, targets in graph.items():
            for target in targets:
                reversed_graph[target].add(source)
        graph = reversed_graph
    indegree = {vertex: 0 for vertex in vertices}
    for targets in graph.values():
        for target in targets:
            indegree[target] += 1
    queue = sorted(vertex for vertex, degree in indegree.items() if degree == 0)
    topo: list[tuple[str, ...]] = []
    cursor = 0
    while cursor < len(queue):
        source = queue[cursor]
        cursor += 1
        topo.append(source)
        for target in sorted(graph[source]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    dag_vertices = set(topo)
    predecessors: dict[tuple[str, ...], set[tuple[str, ...]]] = {
        vertex: set() for vertex in vertices
    }
    for source, targets in graph.items():
        for target in targets:
            predecessors[target].add(source)
    effective_seeds = set(seeds) & dag_vertices
    levels: dict[tuple[str, ...], int] = {}
    for vertex in topo:
        candidates = [
            levels[parent] + 1
            for parent in predecessors[vertex]
            if parent in levels
        ]
        if vertex in effective_seeds:
            levels[vertex] = 0
        elif candidates:
            levels[vertex] = max(candidates)
    return levels, dag_vertices


def build_timing_context(
    pin_rows: list[dict[str, Any]],
    io_rows: list[dict[str, Any]],
    gate_functions: dict[str, str],
    sdc: dict[str, Any],
    maps: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """由逻辑连接、Liberty角色和SDC构建不依赖Slack的结构性Timing上下文。

    本函数只计算节点特征，不把Pin-Pin虚拟边写入本轮8张CSV。相同的临时有向图用于
    推导startpoint/endpoint逻辑层级和Clock Domain，后续决定Timing边Schema时可复用。
    """

    vertices: set[tuple[str, ...]] = set()
    rows_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    members_by_net: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    pins_by_gate: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for row in pin_rows:
        key = ("pin", row["inst_name"], row["pin_name"])
        vertices.add(key)
        rows_by_key[key] = row
        members_by_net[row["net_name"]].append(key)
        pins_by_gate[row["inst_name"]].append(key)
    for row in io_rows:
        key = ("io_pin", row["iopin_name"])
        vertices.add(key)
        rows_by_key[key] = row
        members_by_net[row["net_name"]].append(key)

    net_adjacency: dict[tuple[str, ...], set[tuple[str, ...]]] = defaultdict(set)
    full_adjacency: dict[tuple[str, ...], set[tuple[str, ...]]] = defaultdict(set)
    for members in members_by_net.values():
        drivers: list[tuple[str, ...]] = []
        sinks: list[tuple[str, ...]] = []
        for key in members:
            row = rows_by_key[key]
            direction = str(row.get("pin_direction", "")).upper()
            is_io = key[0] == "io_pin"
            is_driver = (
                direction in {"INPUT", "INOUT", "FEEDTHRU"}
                if is_io
                else direction in {"OUTPUT", "INOUT", "FEEDTHRU"}
            )
            is_sink = (
                direction in {"OUTPUT", "INOUT", "FEEDTHRU"}
                if is_io
                else direction in {"INPUT", "INOUT", "FEEDTHRU"}
            )
            if is_driver:
                drivers.append(key)
            if is_sink:
                sinks.append(key)
        for source in drivers:
            for target in sinks:
                if source != target:
                    net_adjacency[source].add(target)
                    full_adjacency[source].add(target)

    clock_cell_adjacency: dict[
        tuple[str, ...], set[tuple[str, ...]]
    ] = defaultdict(set)
    for instance, keys in pins_by_gate.items():
        function = gate_functions.get(instance, "UNKNOWN")
        sequential = function in {"SEQUENTIAL_FF", "SEQUENTIAL_LATCH"}
        inputs = [
            key
            for key in keys
            if str(rows_by_key[key].get("pin_direction", "")).upper()
            in {"INPUT", "INOUT", "FEEDTHRU"}
        ]
        outputs = [
            key
            for key in keys
            if str(rows_by_key[key].get("pin_direction", "")).upper()
            in {"OUTPUT", "INOUT", "FEEDTHRU"}
        ]
        if not sequential:
            for source in inputs:
                for target in outputs:
                    if source != target:
                        full_adjacency[source].add(target)
                        if function in {
                            "BUFFER",
                            "INVERTER",
                            "CLOCK_BUFFER",
                            "CLOCK_GATE",
                        }:
                            clock_cell_adjacency[source].add(target)

    startpoints = {
        key
        for key, row in rows_by_key.items()
        if int(row.get("is_timing_startpoint", 0))
    }
    endpoints = {
        key
        for key, row in rows_by_key.items()
        if int(row.get("is_timing_endpoint", 0))
    }
    forward_levels, forward_dag = _longest_dag_levels(
        vertices, full_adjacency, startpoints
    )
    reverse_levels, reverse_dag = _longest_dag_levels(
        vertices, full_adjacency, endpoints, reverse=True
    )

    clock_by_name = {clock["name"]: clock for clock in sdc["clocks"]}
    clock_by_root = {
        port: clock
        for clock in sdc["clocks"]
        for port in clock.get("root_ports", [])
    }
    domain_sets_by_node: dict[tuple[str, ...], set[str]] = defaultdict(set)
    clock_graph: dict[tuple[str, ...], set[tuple[str, ...]]] = defaultdict(set)
    for source, targets in net_adjacency.items():
        clock_graph[source].update(targets)
    for source, targets in clock_cell_adjacency.items():
        clock_graph[source].update(targets)
    queue: list[tuple[str, ...]] = []
    for port, clock in sorted(clock_by_root.items()):
        key = ("io_pin", port)
        if key in vertices:
            domain_sets_by_node[key].add(str(clock["name"]))
            queue.append(key)
    cursor = 0
    while cursor < len(queue):
        source = queue[cursor]
        cursor += 1
        for target in sorted(clock_graph.get(source, set())):
            merged = domain_sets_by_node[target] | domain_sets_by_node[source]
            if merged != domain_sets_by_node[target]:
                domain_sets_by_node[target] = merged
                queue.append(target)

    # 记录真正的Clock Network。后续数据域传播不能把普通数据网络误标为时钟网。
    clock_network_nodes = {
        key for key, domains in domain_sets_by_node.items() if domains
    }
    clock_network_nets = {
        net_name
        for net_name, members in members_by_net.items()
        if any(key in clock_network_nodes for key in members)
    }

    # Sequential Cell的所有Pin继承其Clock Pin所属时钟域。
    for instance, keys in pins_by_gate.items():
        if gate_functions.get(instance) not in {
            "SEQUENTIAL_FF",
            "SEQUENTIAL_LATCH",
        }:
            continue
        domains = {
            domain
            for key in keys
            if rows_by_key[key].get("pin_role") == "CLOCK"
            for domain in domain_sets_by_node.get(key, set())
        }
        if domains:
            for key in keys:
                domain_sets_by_node[key].update(domains)

    # 受某Clock约束的I/O端口继承相应Domain。
    for row in io_rows:
        key = ("io_pin", row["iopin_name"])
        constraint = sdc["input_delays"].get(row["iopin_name"]) or sdc[
            "output_delays"
        ].get(row["iopin_name"])
        if constraint and constraint.get("clock_name") in clock_by_name:
            domain_sets_by_node[key].add(str(constraint["clock_name"]))

    # 从Primary Input和Sequential Q沿结构Timing Graph传播launch domain；遇到
    # Sequential D/Clock时自然停止（全图不包含Sequential Cell内部D->Q弧）。
    queue = sorted(key for key, domains in domain_sets_by_node.items() if domains)
    cursor = 0
    while cursor < len(queue):
        source = queue[cursor]
        cursor += 1
        for target in sorted(full_adjacency.get(source, set())):
            merged = domain_sets_by_node[target] | domain_sets_by_node[source]
            if merged != domain_sets_by_node[target]:
                domain_sets_by_node[target] = merged
                queue.append(target)

    domain_ids = {
        clock["name"]: encode_id(
            maps, "clock_domain_id", str(clock["domain_raw"])
        )
        for clock in sdc["clocks"]
    }
    unknown_domain = encode_id(maps, "clock_domain_id", "UNKNOWN")
    node_features: dict[tuple[str, ...], dict[str, Any]] = {}
    for key in vertices:
        domains = domain_sets_by_node.get(key, set())
        domain_name = next(iter(domains)) if len(domains) == 1 else ""
        clock = clock_by_name.get(domain_name, {})
        node_features[key] = {
            "clock_domain_id": domain_ids.get(domain_name, unknown_domain),
            "clock_period_ns": float(clock.get("period_ns", 0.0)),
            "clock_uncertainty_ns": float(
                clock.get("uncertainty_ns", 0.0)
            ),
            "clock_constraint_valid": int(bool(clock)),
            "timing_forward_level": int(forward_levels.get(key, 0)),
            "timing_reverse_level": int(reverse_levels.get(key, 0)),
            "timing_level_valid": int(
                key in forward_dag
                and key in reverse_dag
                and key in forward_levels
                and key in reverse_levels
            ),
        }
    net_domains: dict[str, set[str]] = defaultdict(set)
    for net_name, members in members_by_net.items():
        for key in members:
            net_domains[net_name].update(domain_sets_by_node.get(key, set()))
    return {
        "node_features": node_features,
        "net_domains": net_domains,
        "clock_network_nets": clock_network_nets,
        "temporary_net_arc_count": sum(
            len(targets) for targets in net_adjacency.values()
        ),
        "temporary_cell_arc_count": sum(
            len(targets)
            for source, targets in full_adjacency.items()
            if targets - net_adjacency.get(source, set())
        ),
        "timing_dag_node_count": len(forward_dag & reverse_dag),
        "timing_cycle_or_unreachable_node_count": len(
            vertices - (forward_dag & reverse_dag)
        ),
    }


def parse_config_mk(path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path or not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        match = re.match(
            r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[:?+]?=\s*(.*)", line
        )
        if match:
            values[match.group(1)] = match.group(2).strip().strip("\"'")
    return values


def parse_design_config_csv(
    path: Path | None, variant_id: int | str | None
) -> tuple[dict[str, str], str]:
    """读取config_csv中指定设计编号的一行。

    动态数据集的每个设计CSV以第一列记录编号（通常列名为“目录”），后续列保留
    该编号对应的config.mk参数。这里按稳定variant_id选择，禁止默认取第一行，以免
    300个样本把不同后端配置错误复用。
    """

    if not path or not path.is_file():
        return {}, ""
    if variant_id is None or str(variant_id).strip() == "":
        raise ValueError(f"配置了design_config_csv但缺少variant_id: {path}")

    def normalized_variant(value: Any) -> str:
        text = str(value).strip()
        try:
            return str(int(float(text)))
        except ValueError:
            return text.lower()

    target = normalized_variant(variant_id)
    with path.open(
        "r", encoding="utf-8-sig", errors="replace", newline=""
    ) as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"设计配置CSV没有表头: {path}")
        fieldnames = [str(name).strip() for name in reader.fieldnames]
        variant_column = fieldnames[0]
        for raw_row in reader:
            row = {
                str(key).strip(): str(value or "").strip()
                for key, value in raw_row.items()
                if key is not None
            }
            if normalized_variant(row.get(variant_column, "")) != target:
                continue
            return (
                {
                    key: value
                    for key, value in row.items()
                    if key != variant_column
                },
                row.get(variant_column, ""),
            )
    raise ValueError(
        f"设计配置CSV找不到variant_id={variant_id}: {path}"
    )


def numeric_or_blank(value: Any) -> float | str:
    try:
        return float(value)
    except (TypeError, ValueError):
        return ""


def normalized_layer_name(layer: str) -> str:
    upper = (layer or "").strip().upper()
    match = re.fullmatch(r"(?:M|MET|METAL)(\d+)", upper)
    if match:
        return f"METAL{int(match.group(1))}"
    match = re.fullmatch(r"VIA(\d+)", upper)
    if match:
        return f"VIA{int(match.group(1))}"
    return upper or "UNKNOWN"


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
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
    print(f"[feature] {path.name}: {len(rows)} rows")


def empty_physical_snapshot() -> dict[str, Any]:
    """返回post-Yosys阶段的空物理快照，保证任何物理特征都只能成为NaN。"""

    return {
        "dbu": 1.0,
        "die": (0, 0, 0, 0),
        "tracks": [],
        "gcell_x": 0,
        "gcell_y": 0,
        "components": {},
        "iopins": {},
        "nets": {},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成四阶段、每阶段8张异构图特征表")
    parser.add_argument("--config", required=True)
    parser.add_argument("--base", default="", help="覆盖 base_graph.pt")
    parser.add_argument(
        "--out-dir",
        default="",
        help="覆盖四阶段输出根目录；默认<output_dir>/stages",
    )
    parser.add_argument(
        "--stage",
        choices=PREDICTION_STAGES,
        default="",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    config_path, cfg = load_config(args.config)
    design = str(cfg.get("design_name") or config_path.stem)
    root_out = output_dir_from_config(config_path, cfg)
    stage_root = (
        Path(args.out_dir).resolve()
        if args.out_dir
        else root_out / "stages"
    )
    if not args.stage:
        # 每个子进程只持有一个阶段允许的数据路径，从执行结构上阻止跨阶段回填。
        for prediction_stage in PREDICTION_STAGES:
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(config_path),
                "--stage",
                prediction_stage,
                "--out-dir",
                str(stage_root),
            ]
            if args.base:
                command.extend(["--base", str(Path(args.base).resolve())])
            print(
                f"[feature] prediction_stage={prediction_stage} starting",
                flush=True,
            )
            subprocess.run(command, check=True)
        print(f"[feature] four-stage bundle completed: {stage_root}")
        return

    prediction_stage = args.stage
    config_key, manifest_artifact, feature_cutoff = STAGE_INPUTS[
        prediction_stage
    ]
    encode_map_path, encoding_maps = load_encode_maps(config_path, cfg)
    congestion_grid_um = resolve_congestion_grid_um(cfg)
    # r2g-skills delta vs upstream R2G2.0 (D11): upstream matched well-tap cells
    # with the literal substring "TAP". gf180's well-tap/endcap masters are
    # named ``__filltie`` / ``__endcap`` and contain no "TAP", so all 324 of
    # them were invisible and nearest_tap_distance_um came out all-NaN. Same
    # per-platform extras this skill already keeps in techlib.profile
    # (_PLATFORM_TAP_EXTRA); make_sample_config.py fills the config field.
    tap_master_patterns = tuple(
        str(token).upper()
        for token in (cfg.get("tap_master_patterns") or ["TAP"])
        if str(token).strip()
    ) or ("TAP",)
    base_path = (
        Path(args.base).resolve()
        if args.base
        else root_out / "base_graph" / "base_graph.pt"
    )
    base = torch.load(base_path, map_location="cpu", weights_only=False)
    if dict(base.cell_type_map) != encoding_maps["cell_type_id"]:
        raise ValueError(
            "base_graph.pt的cell_type_map与当前encode_map.csv不一致；"
            "请使用同一配置重新生成阶段1"
        )

    if config_key is None:
        snapshot_path = None
        snapshot_manifest = {"manifest": "", "semantics": "post_yosys"}
        snapshot = empty_physical_snapshot()
    else:
        snapshot_path = resolve_path(
            config_path, str(cfg.get(config_key, "")), True
        )
        assert snapshot_path is not None and manifest_artifact is not None
        snapshot_manifest = validate_manifest_stage(
            config_path,
            cfg,
            manifest_artifact,
            snapshot_path,
            feature_cutoff,
        )
        snapshot = parse_def(snapshot_path)
    # 旧实现的place/route两个变量均绑定到同一个合法快照。后续计算无法看到Route DEF。
    place = snapshot
    route = snapshot

    lib_raw = cfg.get("lib", "")
    lib_values = lib_raw if isinstance(lib_raw, list) else [lib_raw]
    lib_paths = [
        resolve_path(config_path, str(value), True) for value in lib_values
    ]
    hierarchy_lib_dir_raw = str(
        cfg.get("yosys_hierarchy_lib_dir", "")
    ).strip()
    if hierarchy_lib_dir_raw:
        hierarchy_lib_dir = Path(hierarchy_lib_dir_raw)
        if not hierarchy_lib_dir.is_absolute():
            hierarchy_lib_dir = (
                config_path.parent / hierarchy_lib_dir
            ).resolve()
        if not hierarchy_lib_dir.is_dir():
            raise FileNotFoundError(
                f"宏Liberty目录不存在: {hierarchy_lib_dir}"
            )
        lib_paths.extend(glob_liberty(hierarchy_lib_dir))
    resolved_lib_paths = list(
        dict.fromkeys(
            path.resolve() for path in lib_paths if path is not None
        )
    )
    if not resolved_lib_paths:
        raise ValueError("没有可用于特征提取的Liberty文件")
    lib = parse_liberty(resolved_lib_paths)
    # r2g-skills delta vs upstream R2G2.0 (D8): an empty cell table means every
    # Liberty-derived feature (area, leakage, pin cap, direction, sequential
    # class, clock flag) would silently be 0/""/False for the whole design.
    if not lib["cells"]:
        raise ValueError(
            "Liberty解析结果为空，拒绝生成特征: "
            f"{[str(path) for path in resolved_lib_paths][:5]}\n"
            "HINT: 确认文件是Liberty (支持.lib/.lib.gz)。"
        )
    lef_raw = cfg.get("lef", [])
    lef_values = lef_raw if isinstance(lef_raw, list) else [lef_raw]
    lef_paths = [
        path
        for value in lef_values
        if (path := resolve_path(config_path, str(value), False))
        and path.is_file()
    ]
    # Nangate45把fakeram宏的Pin几何拆在独立LEF中，而常用配置往往只列tech LEF
    # 和标准单元macro LEF。若不补齐这些公用宏LEF，fakeram每个Pin只能退化到宏
    # 原点，Placement拆网后的分段HPWL也无法严格验证。
    lef_search_dirs = {path.parent for path in lef_paths}
    technology_root_raw = str(cfg.get("technology_root", "")).strip()
    if technology_root_raw:
        technology_root = Path(technology_root_raw)
        if not technology_root.is_absolute():
            technology_root = (
                config_path.parent / technology_root
            ).resolve()
        lef_search_dirs.add(technology_root / "lef")
    for lef_dir in sorted(lef_search_dirs):
        if lef_dir.is_dir():
            lef_paths.extend(sorted(lef_dir.glob("fakeram*.lef")))
    lef_paths = list(dict.fromkeys(path.resolve() for path in lef_paths))
    lef = parse_lef_geometry(lef_paths)
    sdc_path = resolve_path(config_path, str(cfg.get("sdc", "")), False)
    sdc = parse_sdc(sdc_path)
    clock_ports = set(sdc["clock_ports"])
    frequency_hz = float(sdc["frequency_hz"])
    design_config_csv_path = resolve_path(
        config_path, str(cfg.get("design_config_csv", "")), False
    )
    config_mk_path = resolve_path(
        config_path, str(cfg.get("config_mk", "")), False
    )
    if design_config_csv_path and design_config_csv_path.is_file():
        flow_cfg, flow_config_row_id = parse_design_config_csv(
            design_config_csv_path, cfg.get("variant_id")
        )
        flow_config_source_path = design_config_csv_path
        flow_config_source_type = "design_config_csv"
    else:
        flow_cfg = parse_config_mk(config_mk_path)
        flow_config_row_id = ""
        flow_config_source_path = config_mk_path
        flow_config_source_type = "config_mk" if config_mk_path else "absent"

    gate_names = list(base.gate_names)
    gate_masters = dict(zip(base.gate_names, base.gate_masters))
    coordinate_trust_stats = apply_coordinate_trust_policy(
        snapshot,
        prediction_stage,
        lef,
        set(gate_names),
    )
    net_names = list(base.net_names)
    connections = list(
        zip(
            base.connection_net_names,
            base.connection_inst_names,
            base.connection_pin_names,
        )
    )
    io_map = {
        name: {"net": net, "direction": direction}
        for name, net, direction in zip(
            base.io_pin_names, base.io_pin_net_names, base.io_pin_directions
        )
    }
    # Resizer可能删除综合网表中的常量单元/Net，也可能插入buffer。基础拓扑仍保持综合
    # 时点；物理量缺失时写valid=0，不能伪造坐标，也不能悄悄删除基础实体。
    missing_gates = [name for name in gate_names if name not in place["components"]]
    missing_nets = [name for name in net_names if name not in snapshot["nets"]]
    # 物理流程可能新增buffer/CTS/filler及其Net。它们不属于综合基础图，只记录审计数。
    place_extra_gates = set(place["components"]) - set(gate_names)
    route_extra_nets = set(snapshot["nets"]) - set(net_names)

    cell_type_map = dict(base.cell_type_map)
    die = place["die"]
    die_um = (
        die[0] / place["dbu"],
        die[1] / place["dbu"],
        die[2] / place["dbu"],
        die[3] / place["dbu"],
    )
    physical_snapshot_valid = snapshot_path is not None
    die_width = (
        die_um[2] - die_um[0] if physical_snapshot_valid else float("nan")
    )
    die_height = (
        die_um[3] - die_um[1] if physical_snapshot_valid else float("nan")
    )
    gate_rows: list[dict[str, Any]] = []
    gate_functions: dict[str, str] = {}
    for name in gate_names:
        master = gate_masters[name]
        component = place["components"].get(name, {})
        placement_valid = int(
            component.get("x") is not None and component.get("y") is not None
        )
        lib_cell = lib["cells"].get(master.upper(), {})
        function_name = cell_function_name(master, lib_cell)
        gate_functions[name] = function_name
        lef_macro = lef.get(master.upper(), {})
        oriented_width, oriented_height = oriented_size(
            float(lef_macro.get("width", 0.0)),
            float(lef_macro.get("height", 0.0)),
            str(component.get("orient", "N")),
        )
        origin_x = (
            float(component["x"]) / place["dbu"]
            if placement_valid
            else float("nan")
        )
        origin_y = (
            float(component["y"]) / place["dbu"]
            if placement_valid
            else float("nan")
        )
        gate_rows.append(
            {
                "graph_id": design,
                "inst_name": name,
                "master": master,
                "cell_type_id": cell_type_map.get(
                    master.upper(), cell_type_map.get("UNKNOWN", -1)
                ),
                "cell_function": function_name,
                "cell_function_id": encode_id(
                    encoding_maps, "cell_function_id", function_name
                ),
                "is_sequential_cell": int(
                    function_name in {"SEQUENTIAL_FF", "SEQUENTIAL_LATCH"}
                ),
                "is_buffer_cell": int(
                    function_name in {"BUFFER", "CLOCK_BUFFER"}
                ),
                "is_inverter_cell": int(function_name == "INVERTER"),
                "is_clock_buffer_cell": int(
                    function_name == "CLOCK_BUFFER"
                ),
                "is_clock_gate_cell": int(function_name == "CLOCK_GATE"),
                "drive_strength": drive_strength(master),
                "cell_area_um2": lib_cell.get("area", 0.0),
                "cell_leakage_power": lib_cell.get("power", 0.0),
                "x_um": origin_x,
                "y_um": origin_y,
                "cell_width_um": oriented_width,
                "cell_height_um": oriented_height,
                "center_x_um": (
                    origin_x + oriented_width / 2.0
                    if placement_valid
                    else float("nan")
                ),
                "center_y_um": (
                    origin_y + oriented_height / 2.0
                    if placement_valid
                    else float("nan")
                ),
                "center_x_normalized": (
                    (origin_x + oriented_width / 2.0 - die_um[0])
                    / max(die_width, 1e-12)
                    if placement_valid
                    else float("nan")
                ),
                "center_y_normalized": (
                    (origin_y + oriented_height / 2.0 - die_um[1])
                    / max(die_height, 1e-12)
                    if placement_valid
                    else float("nan")
                ),
                "orientation": component.get("orient", ""),
                "orientation_id": encode_id(
                    encoding_maps,
                    "orientation_id",
                    str(component.get("orient", "")),
                )
                if placement_valid
                else float("nan"),
                "placement_status": component.get("status", ""),
                "placement_status_id": encode_id(
                    encoding_maps,
                    "placement_status_id",
                    str(component.get("status", "")),
                )
                if placement_valid
                else float("nan"),
                "placement_valid": placement_valid,
            }
        )

    connections_by_net: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for net, instance, pin in connections:
        connections_by_net[net].append((instance, pin))

    io_by_net: dict[str, list[str]] = defaultdict(list)
    io_rows: list[dict[str, Any]] = []
    tap_positions = [
        (
            float(row.get("x") or 0) / place["dbu"],
            float(row.get("y") or 0) / place["dbu"],
        )
        for row in place["components"].values()
        if any(
            token in str(row.get("master", "")).upper()
            for token in tap_master_patterns
        )
        and row.get("x") is not None
    ]
    for name in sorted(io_map):
        physical = place["iopins"].get(name, {})
        net_name = canonical_name(physical.get("net") or io_map[name]["net"])
        direction = str(
            physical.get("direction") or io_map[name]["direction"]
        ).upper()
        position_valid = (
            physical.get("x") is not None and physical.get("y") is not None
        )
        x = (
            float(physical["x"]) / place["dbu"]
            if position_valid
            else float("nan")
        )
        y = (
            float(physical["y"]) / place["dbu"]
            if position_valid
            else float("nan")
        )
        nearest_tap = (
            min(math.hypot(x - tx, y - ty) for tx, ty in tap_positions)
            if tap_positions
            else float("nan")
        )
        io_by_net[net_name].append(name)
        use = str(physical.get("use", "SIGNAL"))
        net_type = net_type_name(net_name, use, clock_ports)
        layer_name = normalized_layer_name(str(physical.get("layer", "")))
        is_clock_port = name in clock_ports
        role_name = io_pin_role_name(direction, is_clock_port)
        input_constraint = sdc["input_delays"].get(name, {})
        output_constraint = sdc["output_delays"].get(name, {})
        row = {
            "graph_id": design,
            "iopin_name": name,
            "net_name": net_name,
            "pin_x_um": x,
            "pin_y_um": y,
            "pin_direction": direction,
            "pin_direction_id": direction_id(direction, encoding_maps),
            "pin_role": role_name,
            "pin_role_id": encode_id(
                encoding_maps, "pin_role_id", role_name
            ),
            "is_clock_port": int(is_clock_port),
            "is_driver_pin": int(
                direction in {"INPUT", "INOUT", "FEEDTHRU"}
            ),
            "is_sink_pin": int(
                direction in {"OUTPUT", "INOUT", "FEEDTHRU"}
            ),
            "is_timing_startpoint": int(
                direction in {"INPUT", "INOUT", "FEEDTHRU"}
                and not is_clock_port
            ),
            "is_timing_endpoint": int(
                direction in {"OUTPUT", "INOUT", "FEEDTHRU"}
            ),
            "input_delay_ns": float(input_constraint.get("delay_ns", 0.0)),
            "output_delay_ns": float(output_constraint.get("delay_ns", 0.0)),
            "io_constraint_valid": int(
                bool(input_constraint or output_constraint)
            ),
            "pin_layer_hint": physical.get("layer", ""),
            "pin_layer": layer_name,
            "pin_layer_id": encode_id(
                encoding_maps, "pin_layer_id", layer_name
            )
            if physical.get("layer")
            else float("nan"),
            "nearest_tap_distance_um": nearest_tap,
            "net_type": net_type,
            "net_type_id": encode_id(
                encoding_maps, "net_type_id", net_type
            ),
        }
        row.update(
            point_position_features(x, y, position_valid, die_um)
        )
        io_rows.append(row)

    edge_gp_rows: list[dict[str, Any]] = []
    edge_pn_rows: list[dict[str, Any]] = []
    pin_rows: list[dict[str, Any]] = []
    total_pin_cap = 0.0
    for net_name, instance, pin_name in connections:
        master = gate_masters[instance]
        pin_info = lib["cells"].get(master.upper(), {}).get("pins", {}).get(
            pin_name, {}
        )
        cap = float(pin_info.get("cap_fF", 0.0))
        total_pin_cap += cap
        ptype_name = pin_type_name(master, pin_name, lib)
        ptype = encode_id(encoding_maps, "pin_type_id", ptype_name)
        pin_direction = (
            str(pin_info.get("direction", "")).upper() or "UNKNOWN"
        )
        function_name = gate_functions.get(instance, "UNKNOWN")
        role_name = pin_role_name(
            ptype_name, pin_direction, function_name
        )
        role_id = encode_id(
            encoding_maps, "pin_role_id", role_name
        )
        ntype_name = net_type_name(
            net_name,
            route["nets"].get(net_name, {}).get("use", ""),
            clock_ports,
        )
        ntype = encode_id(encoding_maps, "net_type_id", ntype_name)
        ctype = cell_type_map.get(
            master.upper(), cell_type_map.get("UNKNOWN", -1)
        )
        pin_row = {
            "graph_id": design,
            "inst_name": instance,
            "pin_name": pin_name,
            "net_name": net_name,
            "pin_type": ptype_name,
            "pin_type_id": ptype,
            "pin_role": role_name,
            "pin_role_id": role_id,
            "pin_direction": pin_direction,
            "pin_direction_id": direction_id(
                pin_direction, encoding_maps
            ),
            "pin_cap_fF": cap,
            "pin_max_transition_ns": float(
                pin_info.get("max_transition_ns", 0.0)
            ),
            "pin_max_capacitance_fF": float(
                pin_info.get("max_capacitance_fF", 0.0)
            ),
            "cell_type_id": ctype,
            "cell_function_id": encode_id(
                encoding_maps, "cell_function_id", function_name
            ),
            "owner_drive_strength": drive_strength(master),
            "is_clock_pin": int(role_name == "CLOCK"),
            "is_data_pin": int(role_name == "DATA"),
            "is_reset_pin": int(role_name == "RESET"),
            "is_set_pin": int(role_name == "SET"),
            "is_enable_pin": int(role_name == "ENABLE"),
            "is_sequential_pin": int(
                function_name in {"SEQUENTIAL_FF", "SEQUENTIAL_LATCH"}
            ),
            "is_combinational_pin": int(
                function_name
                not in {
                    "SEQUENTIAL_FF",
                    "SEQUENTIAL_LATCH",
                    "PHYSICAL_ONLY",
                    "UNKNOWN",
                }
            ),
            "is_driver_pin": int(
                pin_direction in {"OUTPUT", "INOUT", "FEEDTHRU"}
            ),
            "is_sink_pin": int(
                pin_direction in {"INPUT", "INOUT", "FEEDTHRU"}
            ),
            "is_timing_startpoint": int(role_name == "Q"),
            "is_timing_endpoint": int(role_name == "DATA"),
        }
        pin_row.update(
            pin_position_features(
                place["components"].get(instance),
                pin_name,
                place["dbu"],
                lef,
                die_um,
            )
        )
        pin_rows.append(pin_row)
        edge_gp_rows.append(
            {
                "graph_id": design,
                "inst_name": instance,
                "pin_name": pin_name,
                "cell_type_id": ctype,
                "pin_type": ptype_name,
                "pin_type_id": ptype,
                "pin_role": role_name,
                "pin_role_id": role_id,
                "cell_function_id": encode_id(
                    encoding_maps, "cell_function_id", function_name
                ),
            }
        )
        edge_pn_rows.append(
            {
                "graph_id": design,
                "inst_name": instance,
                "pin_name": pin_name,
                "net_name": net_name,
                "pin_type": ptype_name,
                "pin_type_id": ptype,
                "pin_role": role_name,
                "pin_role_id": role_id,
                "pin_direction_id": direction_id(
                    pin_direction, encoding_maps
                ),
                "is_driver_pin": pin_row["is_driver_pin"],
                "is_sink_pin": pin_row["is_sink_pin"],
                "net_type": ntype_name,
                "net_type_id": ntype,
            }
        )

    edge_io_rows = [
        {
            "graph_id": design,
            "iopin_name": row["iopin_name"],
            "net_name": row["net_name"],
            "pin_direction": row["pin_direction"],
            "pin_direction_id": row["pin_direction_id"],
            "pin_role": row["pin_role"],
            "pin_role_id": row["pin_role_id"],
            "is_driver_pin": row["is_driver_pin"],
            "is_sink_pin": row["is_sink_pin"],
            "clock_domain_id": encode_id(
                encoding_maps, "clock_domain_id", "UNKNOWN"
            ),
            "net_type": row["net_type"],
            "net_type_id": row["net_type_id"],
        }
        for row in io_rows
        if row["net_name"] in set(net_names)
    ]

    timing_context = build_timing_context(
        pin_rows,
        io_rows,
        gate_functions,
        sdc,
        encoding_maps,
    )
    timing_node_features = timing_context["node_features"]
    for row in pin_rows:
        row.update(
            timing_node_features[
                ("pin", row["inst_name"], row["pin_name"])
            ]
        )
    for row in io_rows:
        row.update(
            timing_node_features[("io_pin", row["iopin_name"])]
        )
    io_features_by_name = {row["iopin_name"]: row for row in io_rows}
    for row in edge_io_rows:
        row["clock_domain_id"] = io_features_by_name[
            row["iopin_name"]
        ]["clock_domain_id"]

    pin_rows_by_gate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pin_rows:
        pin_rows_by_gate[row["inst_name"]].append(row)
    unknown_domain_id = encode_id(
        encoding_maps, "clock_domain_id", "UNKNOWN"
    )
    for row in gate_rows:
        owned_pins = pin_rows_by_gate.get(row["inst_name"], [])
        valid_pins = [
            pin for pin in owned_pins if pin["timing_level_valid"]
        ]
        domains = {
            int(pin["clock_domain_id"])
            for pin in owned_pins
            if int(pin["clock_domain_id"]) != unknown_domain_id
        }
        row["timing_forward_level"] = (
            max(int(pin["timing_forward_level"]) for pin in valid_pins)
            if valid_pins
            else 0
        )
        row["timing_reverse_level"] = (
            max(int(pin["timing_reverse_level"]) for pin in valid_pins)
            if valid_pins
            else 0
        )
        row["timing_level_valid"] = int(bool(valid_pins))
        row["clock_domain_id"] = (
            next(iter(domains)) if len(domains) == 1 else unknown_domain_id
        )

    io_only_nets: dict[str, list[str]] = defaultdict(list)
    for net_name, iopins in io_by_net.items():
        if net_name not in connections_by_net:
            io_only_nets[net_name].extend(iopins)

    congestion_features, congestion_feature_stats = compute_congestion_features(
        gate_names,
        gate_masters,
        place,
        route,
        lef,
        connections_by_net,
        io_by_net,
        io_only_nets,
        prediction_stage,
        congestion_grid_um,
    )
    for row in gate_rows:
        row.update(congestion_features[row["inst_name"]])

    # Canonical Net节点数量和拓扑保持不变。稳定端点先找出直接小网，再沿后端新增
    # buffer/clock cell扩展，把没有原始端点的中间小网也反标给唯一的base Net。
    if snapshot_path is None:
        base_endpoint_counts: dict[str, int] = defaultdict(int)
        for net_name, _, _ in connections:
            base_endpoint_counts[net_name] += 1
        for info in io_map.values():
            base_net_name = canonical_name(str(info.get("net", "")))
            if base_net_name:
                base_endpoint_counts[base_net_name] += 1
        lineage_records = {
            net_name: {
                "base_endpoint_count": base_endpoint_counts.get(net_name, 0),
                "mapped_base_endpoint_count": base_endpoint_counts.get(
                    net_name, 0
                ),
                "anchor_coverage": (
                    1.0 if base_endpoint_counts.get(net_name, 0) else 0.0
                ),
                "direct_stage_net_count": 1,
                "inferred_backend_stage_net_count": 0,
                "stage_nets": [net_name],
                "stage_net_count": 1,
                "split_flag": 0,
                "renamed_flag": 0,
                "ambiguous_component_count": 0,
                "lineage_valid": 1,
            }
            for net_name in net_names
        }
        lineage_summary = {
            "base_nets_with_inferred_backend_stage_nets": 0,
            "inferred_backend_stage_net_count": 0,
            "ambiguous_stage_net_count": 0,
        }
    else:
        lineage = build_base_to_stage_lineage(base, snapshot)
        lineage_records = lineage["records"]
        lineage_summary = lineage["summary"]
    stage_net_geometry_cache: dict[str, dict[str, Any]] = {}
    stage_net_alignment: dict[str, dict[str, Any]] = {}
    stage_nets_by_base: dict[str, list[str]] = {}
    for base_net_name in net_names:
        record = lineage_records[base_net_name]
        stage_net_names = list(record["stage_nets"])
        stage_nets_by_base[base_net_name] = stage_net_names
        anchor_count = int(record["mapped_base_endpoint_count"])
        anchor_coverage = float(record["anchor_coverage"])
        segment_geometries: list[dict[str, Any]] = []
        if (
            snapshot_path is not None
            and prediction_stage in TRUSTED_STANDARD_CELL_POSITION_STAGES
        ):
            for stage_net_name in stage_net_names:
                if stage_net_name not in stage_net_geometry_cache:
                    stage_net_geometry_cache[stage_net_name] = (
                        stage_net_geometry(stage_net_name, snapshot, lef)
                    )
                segment_geometries.append(
                    stage_net_geometry_cache[stage_net_name]
                )
        segment_hpwl = [
            float(item["hpwl_um"])
            for item in segment_geometries
            if int(item["valid"]) == 1
        ]
        segment_valid = int(
            bool(stage_net_names)
            and len(segment_hpwl) == len(stage_net_names)
            and int(record["lineage_valid"]) == 1
        )
        stage_net_alignment[base_net_name] = {
            "stage_net_count": int(record["stage_net_count"]),
            "stage_direct_net_count": int(record["direct_stage_net_count"]),
            "stage_inferred_backend_net_count": int(
                record["inferred_backend_stage_net_count"]
            ),
            "stage_net_split_flag": int(record["split_flag"]),
            "stage_net_renamed_flag": int(record["renamed_flag"]),
            "stage_net_anchor_count": anchor_count,
            "stage_net_anchor_coverage": anchor_coverage,
            "stage_net_alignment_valid": int(
                bool(record["base_endpoint_count"])
                and anchor_count == int(record["base_endpoint_count"])
            ),
            "stage_lineage_ambiguous_flag": int(
                int(record["ambiguous_component_count"]) > 0
            ),
            "stage_lineage_valid": int(record["lineage_valid"]),
            "stage_segment_total_hpwl_um": (
                sum(segment_hpwl) if segment_valid else float("nan")
            ),
            "stage_segment_max_hpwl_um": (
                max(segment_hpwl) if segment_valid else float("nan")
            ),
            "stage_segment_mean_hpwl_um": (
                sum(segment_hpwl) / len(segment_hpwl)
                if segment_valid
                else float("nan")
            ),
            "stage_segment_hpwl_valid": segment_valid,
        }

    net_rows: list[dict[str, Any]] = []
    clock_domain_ids_by_name = {
        clock["name"]: encode_id(
            encoding_maps, "clock_domain_id", clock["domain_raw"]
        )
        for clock in sdc["clocks"]
    }
    for net_name in net_names:
        gate_pins = connections_by_net.get(net_name, [])
        drivers = sinks = macro_flag = 0
        total_sink_cap_ff = 0.0
        for instance, pin_name in gate_pins:
            master = gate_masters[instance]
            pin_info = lib["cells"].get(master.upper(), {}).get("pins", {}).get(
                pin_name, {}
            )
            direction = str(pin_info.get("direction", "")).upper()
            drivers += int(direction in {"OUTPUT", "INOUT", "FEEDTHRU"})
            sinks += int(direction in {"INPUT", "INOUT", "FEEDTHRU"})
            if direction in {"INPUT", "INOUT", "FEEDTHRU"}:
                total_sink_cap_ff += float(pin_info.get("cap_fF", 0.0))
            macro = lef.get(master.upper(), {})
            macro_flag |= int(
                float(macro.get("width", 0.0)) * float(macro.get("height", 0.0))
                > 50.0
                or any(token in master.upper() for token in ("RAM", "ROM", "SRAM"))
            )
        for io_name in io_by_net.get(net_name, []):
            physical = place["iopins"].get(io_name, {})
            direction = str(physical.get("direction", "")).upper()
            # 芯片INPUT端口驱动内部网络，OUTPUT端口是内部网络的sink。
            drivers += int(direction in {"INPUT", "INOUT", "FEEDTHRU"})
            sinks += int(direction in {"OUTPUT", "INOUT", "FEEDTHRU"})
        pin_count = len(gate_pins) + len(io_by_net.get(net_name, []))
        # 物理HPWL只遍历当前阶段对齐到该Canonical Net的全部小网。每条小网先用
        # 自己的完整DEF端点（包含宏Pin、常量单元和后端新增单元）计算bbox/HPWL，
        # 再把小网HPWL累加回base Net；禁止退回只遍历base端点的大网包围盒。
        stage_net_names = stage_nets_by_base[net_name]
        segment_geometries = [
            stage_net_geometry_cache[stage_net_name]
            for stage_net_name in stage_net_names
            if stage_net_name in stage_net_geometry_cache
        ]
        hpwl_valid = bool(
            int(stage_net_alignment[net_name]["stage_segment_hpwl_valid"])
            == 1
            and len(segment_geometries) == len(stage_net_names)
        )
        if hpwl_valid:
            net_left = [
                float(item["net_bbox"][0]) for item in segment_geometries
            ]
            net_lower = [
                float(item["net_bbox"][1]) for item in segment_geometries
            ]
            net_right = [
                float(item["net_bbox"][2]) for item in segment_geometries
            ]
            net_upper = [
                float(item["net_bbox"][3]) for item in segment_geometries
            ]
            # bbox长宽描述全部物理小网的几何并集；HPWL则与Route线长标签一样，
            # 按各条物理小网分别计算后求和，避免在拆分点之间引入不存在的连线。
            bbox_width = max(net_right) - min(net_left)
            bbox_height = max(net_upper) - min(net_lower)
            hpwl = sum(float(item["hpwl_um"]) for item in segment_geometries)
        else:
            bbox_width = float("nan")
            bbox_height = float("nan")
            hpwl = float("nan")
        physical_net_infos = [
            route["nets"][stage_net_name]
            for stage_net_name in stage_nets_by_base[net_name]
            if stage_net_name in route["nets"]
        ]
        route_uses = {
            str(info.get("use", ""))
            for info in physical_net_infos
            if str(info.get("use", ""))
        }
        route_use = "CLOCK" if "CLOCK" in route_uses else (
            next(iter(sorted(route_uses))) if route_uses else ""
        )
        net_type = net_type_name(
            net_name, route_use, clock_ports
        )
        domain_names = timing_context["net_domains"].get(net_name, set())
        net_domain_id = (
            clock_domain_ids_by_name[next(iter(domain_names))]
            if len(domain_names) == 1
            and next(iter(domain_names)) in clock_domain_ids_by_name
            else unknown_domain_id
        )
        net_row = {
                "graph_id": design,
                "net_name": net_name,
                "net_type": net_type,
                "net_type_id": encode_id(
                    encoding_maps, "net_type_id", net_type
                ),
                "fanout": max(0, sinks),
                "pin_count": pin_count,
                "num_drivers": drivers,
                "num_sinks": sinks,
                "connects_macro_flag": macro_flag,
                "is_clock_net": int(
                    net_type == "CLOCK"
                    or net_name in timing_context["clock_network_nets"]
                ),
                "clock_domain_id": net_domain_id,
                "total_sink_cap_fF": total_sink_cap_ff,
                "route_layer_count": (
                    len(
                        set().union(
                            *(
                                set(info.get("layers", set()))
                                for info in physical_net_infos
                            )
                        )
                    )
                    if physical_snapshot_valid
                    else float("nan")
                ),
                "net_bbox_width_um": bbox_width,
                "net_bbox_height_um": bbox_height,
                "hpwl_um": hpwl,
                "hpwl_valid": int(hpwl_valid),
            }
        net_row.update(stage_net_alignment[net_name])
        net_rows.append(net_row)

    net_features_by_name = {row["net_name"]: row for row in net_rows}
    pin_features_by_key = {
        (row["inst_name"], row["pin_name"]): row for row in pin_rows
    }
    for row in edge_pn_rows:
        pin_feature = pin_features_by_key[(row["inst_name"], row["pin_name"])]
        net_feature = net_features_by_name[row["net_name"]]
        row["clock_domain_id"] = pin_feature["clock_domain_id"]
        row["net_fanout"] = net_feature["fanout"]
        row["total_sink_cap_fF"] = net_feature["total_sink_cap_fF"]
    for row in edge_io_rows:
        net_feature = net_features_by_name[row["net_name"]]
        row["net_fanout"] = net_feature["fanout"]
        row["total_sink_cap_fF"] = net_feature["total_sink_cap_fF"]

    fanouts = [row["fanout"] for row in net_rows]
    route_layers = [row["route_layer_count"] for row in net_rows]
    clock_periods = [
        float(clock["period_ns"])
        for clock in sdc["clocks"]
        if float(clock["period_ns"]) > 0
    ]
    place_density_raw = flow_cfg.get("PLACE_DENSITY", "")
    metadata_rows = [
        {
            "graph_id": design,
            "num_logical_cells": len(gate_rows),
            "num_logical_nets": len(net_rows),
            "num_ios": len(io_rows),
            "avg_fanout": sum(fanouts) / len(fanouts) if fanouts else 0.0,
            "die_width_um": die_width,
            "die_height_um": die_height,
            "die_area_um2": (
                die_width * die_height
                if physical_snapshot_valid
                else float("nan")
            ),
            "die_left_um": (
                die[0] / place["dbu"]
                if physical_snapshot_valid
                else float("nan")
            ),
            "die_bottom_um": (
                die[1] / place["dbu"]
                if physical_snapshot_valid
                else float("nan")
            ),
            "die_right_um": (
                die[2] / place["dbu"]
                if physical_snapshot_valid
                else float("nan")
            ),
            "die_top_um": (
                die[3] / place["dbu"]
                if physical_snapshot_valid
                else float("nan")
            ),
            "dbu_per_um": (
                place["dbu"] if physical_snapshot_valid else float("nan")
            ),
            "place_density": numeric_or_blank(place_density_raw),
            "place_density_is_default": int(
                str(place_density_raw).lower() in {"", "default"}
            ),
            "core_utilization": numeric_or_blank(
                flow_cfg.get("CORE_UTILIZATION", "")
            ),
            "abc_area": numeric_or_blank(flow_cfg.get("ABC_AREA", "")),
            "total_lib_pin_cap_fF": total_pin_cap,
            "avg_route_layers_per_net": (
                sum(route_layers) / len(route_layers) if route_layers else 0.0
            ),
            "avg_tracks_per_layer": (
                sum(route["tracks"]) / len(route["tracks"])
                if route["tracks"]
                else 0.0
            ),
            "v_nom": lib.get("v_nom") or 0.0,
            "freq_hz": frequency_hz,
            "num_clocks": len(sdc["clocks"]),
            "min_clock_period_ns": min(clock_periods) if clock_periods else 0.0,
            "max_clock_period_ns": max(clock_periods) if clock_periods else 0.0,
            "avg_clock_period_ns": (
                sum(clock_periods) / len(clock_periods)
                if clock_periods
                else 0.0
            ),
            "liberty_source_count": len(resolved_lib_paths),
            "liberty_source_paths": ";".join(
                str(path) for path in resolved_lib_paths
            ),
            "liberty_cell_count": len(lib["cells"]),
            "sdc_source_path": str(sdc_path or ""),
            "topology_source_stage": "post_synthesis",
            "prediction_stage": prediction_stage,
            "feature_cutoff": feature_cutoff,
            "feature_source_path": str(snapshot_path or ""),
            "coordinate_source_stage": feature_cutoff,
            "coordinate_trust_policy": coordinate_trust_stats["policy"],
            "standard_cell_coordinates_trusted": coordinate_trust_stats[
                "standard_cell_coordinates_trusted"
            ],
            "gate_geom_edge_eligible": coordinate_trust_stats[
                "gate_geom_edge_eligible"
            ],
            "hpwl_feature_eligible": coordinate_trust_stats[
                "hpwl_feature_eligible"
            ],
            "congestion_feature_eligible": coordinate_trust_stats[
                "congestion_feature_eligible"
            ],
            "raw_positioned_component_count": coordinate_trust_stats[
                "raw_positioned_component_count"
            ],
            "trusted_positioned_component_count": coordinate_trust_stats[
                "trusted_positioned_component_count"
            ],
            "filtered_placeholder_component_count": coordinate_trust_stats[
                "filtered_placeholder_component_count"
            ],
            "raw_positioned_base_gate_count": coordinate_trust_stats[
                "raw_positioned_base_gate_count"
            ],
            "trusted_positioned_base_gate_count": coordinate_trust_stats[
                "trusted_positioned_base_gate_count"
            ],
            "trusted_fixed_component_count": coordinate_trust_stats[
                "trusted_fixed_component_count"
            ],
            "trusted_macro_component_count": coordinate_trust_stats[
                "trusted_macro_component_count"
            ],
            "hpwl_source_stage": feature_cutoff,
            "hpwl_source_path": str(snapshot_path or ""),
            "route_feature_source_path": "",
            "flow_config_source_type": flow_config_source_type,
            "flow_config_source_path": str(flow_config_source_path or ""),
            "flow_config_row_id": flow_config_row_id,
            "raw_manifest_path": snapshot_manifest["manifest"],
            "feature_manifest_semantics": snapshot_manifest["semantics"],
            "congestion_feature_source_stage": congestion_feature_stats[
                "source_stage"
            ],
            "congestion_grid_spec_source_stage": congestion_feature_stats[
                "grid_spec_source_stage"
            ],
            "congestion_grid_step_x_um": congestion_feature_stats[
                "grid_step_x_um"
            ],
            "congestion_grid_step_y_um": congestion_feature_stats[
                "grid_step_y_um"
            ],
            "congestion_grid_count_x": congestion_feature_stats["grid_count_x"],
            "congestion_grid_count_y": congestion_feature_stats["grid_count_y"],
            "congestion_grid_step_dbu": congestion_feature_stats[
                "grid_step_dbu"
            ],
            "encode_map_path": str(encode_map_path),
            "stage_extra_components_excluded": len(place_extra_gates),
            "stage_extra_nets_excluded": len(route_extra_nets),
            "synthesis_gates_missing_in_stage": len(missing_gates),
            "synthesis_nets_missing_in_stage": len(missing_nets),
            "io_only_net_count": len(io_only_nets),
            "stage_split_base_net_count": sum(
                int(row["stage_net_split_flag"])
                for row in stage_net_alignment.values()
            ),
            "stage_renamed_base_net_count": sum(
                int(row["stage_net_renamed_flag"])
                for row in stage_net_alignment.values()
            ),
            "stage_unaligned_base_net_count": sum(
                not int(row["stage_net_alignment_valid"])
                for row in stage_net_alignment.values()
            ),
            "stage_base_nets_with_inferred_backend_nets": lineage_summary[
                "base_nets_with_inferred_backend_stage_nets"
            ],
            "stage_inferred_backend_net_count": lineage_summary[
                "inferred_backend_stage_net_count"
            ],
            "stage_ambiguous_net_count": lineage_summary[
                "ambiguous_stage_net_count"
            ],
            "timing_temporary_net_arc_count": timing_context[
                "temporary_net_arc_count"
            ],
            "timing_temporary_cell_arc_count": timing_context[
                "temporary_cell_arc_count"
            ],
            "timing_dag_node_count": timing_context[
                "timing_dag_node_count"
            ],
            "timing_cycle_or_unreachable_node_count": timing_context[
                "timing_cycle_or_unreachable_node_count"
            ],
        }
    ]

    out_dir = stage_root / prediction_stage / "features"
    write_rows(out_dir / "metadata.csv", list(metadata_rows[0]), metadata_rows)
    write_rows(
        out_dir / "nodes_gate.csv",
        [
            "graph_id",
            "inst_name",
            "master",
            "cell_type_id",
            "cell_function",
            "cell_function_id",
            "is_sequential_cell",
            "is_buffer_cell",
            "is_inverter_cell",
            "is_clock_buffer_cell",
            "is_clock_gate_cell",
            "drive_strength",
            "cell_area_um2",
            "cell_leakage_power",
            "x_um",
            "y_um",
            "cell_width_um",
            "cell_height_um",
            "center_x_um",
            "center_y_um",
            "center_x_normalized",
            "center_y_normalized",
            "orientation",
            "orientation_id",
            "placement_status",
            "placement_status_id",
            "placement_valid",
            "clock_domain_id",
            "timing_forward_level",
            "timing_reverse_level",
            "timing_level_valid",
            "congestion_pin_density",
            "congestion_cell_density",
            "congestion_net_density",
            "congestion_rudy",
            "congestion_rudy_pin",
            "congestion_feature_valid",
        ],
        gate_rows,
    )
    write_rows(
        out_dir / "nodes_net.csv",
        [
            "graph_id",
            "net_name",
            "net_type",
            "net_type_id",
            "fanout",
            "pin_count",
            "num_drivers",
            "num_sinks",
            "connects_macro_flag",
            "is_clock_net",
            "clock_domain_id",
            "total_sink_cap_fF",
            "route_layer_count",
            "net_bbox_width_um",
            "net_bbox_height_um",
            "hpwl_um",
            "hpwl_valid",
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
            "stage_segment_hpwl_valid",
        ],
        net_rows,
    )
    write_rows(
        out_dir / "nodes_iopin.csv",
        [
            "graph_id",
            "iopin_name",
            "net_name",
            "pin_x_um",
            "pin_y_um",
            "pin_direction",
            "pin_direction_id",
            "pin_role",
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
            "pin_layer_hint",
            "pin_layer",
            "pin_layer_id",
            "nearest_tap_distance_um",
            "pin_x_normalized",
            "pin_y_normalized",
            "distance_to_die_left_um",
            "distance_to_die_right_um",
            "distance_to_die_bottom_um",
            "distance_to_die_top_um",
            "pin_position_valid",
            "timing_forward_level",
            "timing_reverse_level",
            "timing_level_valid",
            "net_type",
            "net_type_id",
        ],
        io_rows,
    )
    write_rows(
        out_dir / "nodes_pin.csv",
        [
            "graph_id",
            "inst_name",
            "pin_name",
            "net_name",
            "pin_type",
            "pin_type_id",
            "pin_role",
            "pin_role_id",
            "pin_direction",
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
            "pin_x_um",
            "pin_y_um",
            "pin_x_normalized",
            "pin_y_normalized",
            "distance_to_die_left_um",
            "distance_to_die_right_um",
            "distance_to_die_bottom_um",
            "distance_to_die_top_um",
            "pin_position_valid",
            "timing_forward_level",
            "timing_reverse_level",
            "timing_level_valid",
        ],
        pin_rows,
    )
    write_rows(
        out_dir / "edges_gate_pin.csv",
        [
            "graph_id",
            "inst_name",
            "pin_name",
            "cell_type_id",
            "pin_type",
            "pin_type_id",
            "pin_role",
            "pin_role_id",
            "cell_function_id",
        ],
        edge_gp_rows,
    )
    write_rows(
        out_dir / "edges_pin_net.csv",
        [
            "graph_id",
            "inst_name",
            "pin_name",
            "net_name",
            "pin_type",
            "pin_type_id",
            "pin_role",
            "pin_role_id",
            "pin_direction_id",
            "is_driver_pin",
            "is_sink_pin",
            "clock_domain_id",
            "net_type",
            "net_type_id",
            "net_fanout",
            "total_sink_cap_fF",
        ],
        edge_pn_rows,
    )
    write_rows(
        out_dir / "edges_iopin_net.csv",
        [
            "graph_id",
            "iopin_name",
            "net_name",
            "pin_direction",
            "pin_direction_id",
            "pin_role",
            "pin_role_id",
            "is_driver_pin",
            "is_sink_pin",
            "clock_domain_id",
            "net_type",
            "net_type_id",
            "net_fanout",
            "total_sink_cap_fF",
        ],
        edge_io_rows,
    )
    print(
        f"[feature] completed: {out_dir}; prediction_stage={prediction_stage} "
        f"feature_cutoff={feature_cutoff}; "
        f"source={snapshot_path.name if snapshot_path else 'post_yosys'}; "
        f"excluded_stage_components={len(place_extra_gates)} "
        f"excluded_stage_nets={len(route_extra_nets)}; "
        f"missing_stage_gates={len(missing_gates)} "
        f"missing_stage_nets={len(missing_nets)}"
    )


if __name__ == "__main__":
    main()
