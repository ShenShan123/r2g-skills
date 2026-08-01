#!/usr/bin/env python3
"""阶段1：从综合后 Verilog 直接生成 Gate→Gate 基础图。

综合网表定义逻辑Gate、Net、顶层IO和Gate-Pin-Net incidence，不生成中间.net文件。
Floorplan及后续DEF不会被阶段1读取。最终.pt同时保存Gate→Gate投影边和原始
incidence，供阶段2/4按稳定实体键组装异构图。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import torch
from torch_geometric.data import Data


def canonical_name(value: str) -> str:
    """统一 Verilog/DEF 转义形式。

    Yosys 常写 ``\foo[0]``，DEF 常写 ``foo\[0\]``。去除反斜杠后两者才能按实体键对齐。
    """

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


def resolve_path(config_path: Path, raw: str, required: bool = True) -> Path | None:
    if not raw:
        if required:
            raise ValueError(f"配置缺少必要路径: {raw!r}")
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    if required and not path.is_file():
        raise FileNotFoundError(path)
    return path


def load_config(path: str) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        cfg = json.load(handle)
    if not isinstance(cfg, dict):
        raise TypeError("配置 JSON 必须是对象")
    return config_path, cfg


def load_encode_maps(
    config_path: Path, cfg: dict[str, Any]
) -> tuple[Path, dict[str, dict[str, int]], list[dict[str, str]]]:
    """读取统一编码表；编号只能由CSV定义，脚本不得自行enumerate。"""

    encode_path = resolve_path(
        config_path, str(cfg.get("encode_map", "encode_map.csv")), True
    )
    assert encode_path is not None
    with encode_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {
        "map_name",
        "raw_value",
        "encoded_id",
        "technology",
        "source",
        "physical_meaning",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError(
            f"encode_map字段不完整，应包含 {sorted(required)}: {encode_path}"
        )
    platform = str(cfg.get("platform", "")).strip().lower()
    maps: dict[str, dict[str, int]] = defaultdict(dict)
    selected_rows: list[dict[str, str]] = []
    for row in rows:
        technology = row["technology"].strip().lower()
        if technology not in {"global", "*", platform}:
            continue
        map_name = row["map_name"].strip()
        raw_value = row["raw_value"].strip().upper()
        try:
            encoded_id = int(row["encoded_id"])
        except ValueError as error:
            raise ValueError(f"encode_map encoded_id不是整数: {row}") from error
        previous = maps[map_name].get(raw_value)
        if previous is not None and previous != encoded_id:
            raise ValueError(
                f"encode_map存在冲突: {map_name}/{raw_value}={previous},{encoded_id}"
            )
        maps[map_name][raw_value] = encoded_id
        selected_rows.append(dict(row))
    if "cell_type_id" not in maps:
        raise ValueError(
            f"encode_map没有technology={platform!r}的cell_type_id"
        )
    return encode_path, dict(maps), selected_rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_torch_save(value: Any, path: Path) -> None:
    """同目录临时文件落盘后原子替换，避免中断留下半写PT。"""

    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        torch.save(value, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_raw_manifest(
    config_path: Path, cfg: dict[str, Any]
) -> dict[str, Any]:
    """校验动态数据集manifest中的身份、路径和哈希，防止配置串样本/串阶段。"""

    raw = str(cfg.get("raw_manifest", ""))
    if not raw:
        return {"enabled": False}
    manifest_path = resolve_path(config_path, raw, True)
    assert manifest_path is not None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_sample = str(cfg.get("design_name", ""))
    if expected_sample and manifest.get("sample_id") != expected_sample:
        raise ValueError(
            f"manifest sample_id={manifest.get('sample_id')!r} "
            f"与 design_name={expected_sample!r} 不一致"
        )
    expected_top = str(cfg.get("top_module", ""))
    if expected_top and manifest.get("top_module") != expected_top:
        raise ValueError("manifest top_module 与配置不一致")
    expected_platform = str(cfg.get("platform", ""))
    if expected_platform and manifest.get("platform") != expected_platform:
        raise ValueError("manifest platform 与配置不一致")

    field_map = {
        "yosys_v": "yosys_netlist",
        "floorplan_def": "floorplan_def",
        "place_def": "placement_def",
        "cts_def": "cts_def",
        "route_def": "routing_def",
        "spef": "final_spef",
    }
    verified: dict[str, dict[str, str]] = {}
    artifacts = manifest.get("artifacts", {})
    for config_field, artifact_name in field_map.items():
        configured = str(cfg.get(config_field, ""))
        if not configured:
            continue
        artifact = artifacts.get(artifact_name)
        if not artifact:
            raise ValueError(f"manifest 缺少 artifact={artifact_name}")
        configured_path = resolve_path(config_path, configured, True)
        manifest_file = (manifest_path.parent / artifact["path"]).resolve()
        if configured_path != manifest_file:
            raise ValueError(
                f"{config_field} 未指向manifest文件: "
                f"config={configured_path}, manifest={manifest_file}"
            )
        actual_hash = sha256_file(configured_path)
        if actual_hash != artifact.get("sha256"):
            raise ValueError(f"{config_field} SHA256与manifest不一致")
        verified[config_field] = {
            "artifact": artifact_name,
            "semantics": str(artifact.get("semantics", "")),
            "sha256": actual_hash,
        }
    return {
        "enabled": True,
        "manifest": str(manifest_path),
        "sample_id": str(manifest.get("sample_id", "")),
        "platform": str(manifest.get("platform", "")),
        "verified_artifacts": verified,
    }


def hierarchy_key(value: str) -> str:
    """生成只用于跨工具名称对齐的层次键。

    Yosys强制flatten后用``.``连接新增层次，OpenROAD DEF通常用``/``。原始转义实例
    名本身仍可能带``.``，因此不能直接全局修改正式实体名；这里只把两者折叠成比较键，
    最终仍优先保存DEF中已经存在的正式拼写。
    """

    return re.sub(r"[./]", "/", canonical_name(value))


def yosys_quote(value: str | Path) -> str:
    """将路径/标识符安全放入Yosys ``-p``命令字符串。"""

    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def unique_reference_index(
    values: list[str] | set[str],
    entity: str,
) -> dict[str, str]:
    """按层次键建立唯一索引；歧义必须报错，禁止猜测实体对应关系。"""

    grouped: dict[str, list[str]] = defaultdict(list)
    for value in values:
        grouped[hierarchy_key(value)].append(value)
    ambiguous = {
        key: sorted(set(items))
        for key, items in grouped.items()
        if len(set(items)) > 1
    }
    if ambiguous:
        examples = list(ambiguous.items())[:5]
        raise ValueError(f"{entity}层次名称归一化后不唯一: {examples}")
    return {key: items[0] for key, items in grouped.items()}


def vector_reference_names(
    values: list[str] | set[str],
) -> dict[str, list[tuple[int, str]]]:
    """收集DEF中的``base[index]``，用于恢复非零起始下标的总线。"""

    grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for value in values:
        match = re.match(r"^(.*)\[(-?\d+)\]$", value)
        if match:
            grouped[hierarchy_key(match.group(1))].append(
                (int(match.group(2)), value)
            )
    return {
        key: sorted(set(items), key=lambda item: (item[0], item[1]))
        for key, items in grouped.items()
    }


def resolve_yosys_hierarchy_libs(
    config_path: Path,
    cfg: dict[str, Any],
    primary_libs: list[Path],
) -> list[Path]:
    """确定Yosys层次展开所需的标准单元和宏单元Liberty。

    ``lib``通常只含标准单元库，但大型设计还实例化fakeram宏。默认同时读取主Liberty
    所在目录下全部``*.lib``，只把它们作为黑盒接口，不把宏内部实现引入基础图。
    """

    explicit = cfg.get("yosys_hierarchy_libs", [])
    if isinstance(explicit, str):
        explicit = [explicit] if explicit else []
    paths = list(primary_libs)
    for raw in explicit:
        resolved = resolve_path(config_path, str(raw), True)
        assert resolved is not None
        paths.append(resolved)

    # r2g-skills delta vs upstream R2G2.0 (D9): upstream ALWAYS scanned the
    # primary Liberty's own directory when ``yosys_hierarchy_lib_dir`` was
    # absent. That is right only where a platform's lib/ holds one library plus
    # its macros (nangate45: 1 std-cell lib + 22 fakeram). Everywhere else that
    # directory is not a library:
    #   gf180     30 files = TWO cell libraries (7-track AND 9-track) x PVT
    #   sky130hs   2 files = the same library at two temperature corners
    #   sky130hd   2 files = the std-cell lib + a dummy IO lib
    # Scanning it mixes physical libraries and makes every electrical value
    # depend on glob order. It is also INCONSISTENT with stage 02, which only
    # ever scans when the field is set explicitly -- so upstream's stages 01 and
    # 02 could see different cell sets for the same design.
    # The scan is now opt-in and symmetric with 02: set
    # ``yosys_hierarchy_lib_dir`` to a directory, or ``yosys_hierarchy_lib_scan``
    # to restore upstream's implicit behaviour. ORFS already resolves macro
    # Liberty into ADDITIONAL_LIBS, which the adapter puts in ``lib``.
    raw_dir = str(cfg.get("yosys_hierarchy_lib_dir", "")).strip()
    lib_dir: Path | None = None
    if raw_dir:
        lib_dir = Path(raw_dir)
        if not lib_dir.is_absolute():
            lib_dir = (config_path.parent / lib_dir).resolve()
    elif bool(cfg.get("yosys_hierarchy_lib_scan", False)):
        lib_dir = primary_libs[0].parent
    if lib_dir is not None:
        if not lib_dir.is_dir():
            raise FileNotFoundError(f"Yosys层次展开Liberty目录不存在: {lib_dir}")
        paths.extend(glob_liberty(lib_dir))
    unique = list(dict.fromkeys(path.resolve() for path in paths))
    if not unique:
        raise ValueError("Yosys层次展开没有可用Liberty")
    return unique


def parse_synth_verilog_with_yosys(
    path: Path,
    top_module: str,
    liberty_paths: list[Path],
    yosys_bin: str,
    reference_gates: dict[str, str],
    reference_nets: dict[str, list[tuple[str, str]]],
) -> tuple[
    dict[str, str],
    dict[str, list[tuple[str, str]]],
    list[dict[str, str]],
    dict[str, Any],
]:
    """用Yosys结构化JSON将层次综合网表直接展开为稳定Gate-Pin-Net incidence。

    旧解析器只读取顶层module，会把子module误当成一个Gate；它也无法可靠表达总线Pin。
    这里读取Liberty黑盒接口、强制移除``keep_hierarchy``、flatten后导出临时JSON。
    JSON中的连接按bit ID天然完成assign别名合并；临时文件退出函数即删除，不成为数据集
    中间产物，也不会生成历史``.net``文件。
    """

    executable = shutil.which(yosys_bin) if not Path(yosys_bin).is_file() else yosys_bin
    if not executable:
        raise FileNotFoundError(f"找不到Yosys可执行文件: {yosys_bin!r}")
    if not top_module:
        raise ValueError("Yosys层次展开必须显式提供top_module")

    with tempfile.TemporaryDirectory(prefix="r2g2_yosys_flat_") as temp_dir:
        json_path = Path(temp_dir) / "flattened.json"
        commands = [
            *[
                f"read_liberty -lib {yosys_quote(lib_path)}"
                for lib_path in liberty_paths
            ],
            f"read_verilog {yosys_quote(path)}",
            f"hierarchy -check -top {canonical_name(top_module)}",
            "setattr -mod -unset keep_hierarchy",
            "flatten",
            f"write_json {yosys_quote(json_path)}",
        ]
        result = subprocess.run(
            [str(executable), "-q", "-p", "; ".join(commands)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-4000:]
            raise RuntimeError(
                f"Yosys层次展开失败(returncode={result.returncode}): {detail}"
            )
        if not json_path.is_file():
            raise RuntimeError("Yosys返回成功但没有生成结构化JSON")
        payload = json.loads(json_path.read_text(encoding="utf-8"))

    modules = payload.get("modules", {})
    module = modules.get(canonical_name(top_module))
    if not isinstance(module, dict):
        raise ValueError(
            f"Yosys JSON没有top_module={top_module!r}; "
            f"可用module={list(modules)[:10]}"
        )

    gate_reference = unique_reference_index(set(reference_gates), "Gate")
    net_reference = unique_reference_index(set(reference_nets), "Net")
    net_vectors = vector_reference_names(set(reference_nets))

    # 同一个JSON bit可能同时拥有顶层端口名、父module端口名和内部Net名。若其中有名称
    # 能在DEF中唯一命中，就使用DEF正式拼写；否则选择最短的可见综合名称。
    bit_aliases: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for raw_name, detail in module.get("netnames", {}).items():
        name = canonical_name(raw_name)
        bits = list(detail.get("bits", []))
        hidden = int(detail.get("hide_name", 0))
        reference_vector = net_vectors.get(hierarchy_key(name), [])
        indexed_names: list[str]
        if len(bits) > 1 and len(reference_vector) == len(bits):
            indexed_names = [item[1] for item in reference_vector]
        elif len(bits) > 1:
            indexed_names = [f"{name}[{index}]" for index in range(len(bits))]
        else:
            indexed_names = [name]
        for bit, alias in zip(bits, indexed_names):
            if isinstance(bit, int):
                bit_aliases[bit].append((alias, hidden))

    bit_names: dict[int, str] = {}
    ambiguous_bit_names: list[dict[str, Any]] = []
    for bit, aliases in bit_aliases.items():
        matches = list(
            dict.fromkeys(
                net_reference[hierarchy_key(alias)]
                for alias, _ in aliases
                if hierarchy_key(alias) in net_reference
            )
        )
        if len(matches) == 1:
            bit_names[bit] = matches[0]
        elif len(matches) > 1:
            ambiguous_bit_names.append(
                {"bit": bit, "matches": sorted(matches)}
            )
        else:
            bit_names[bit] = min(
                aliases,
                key=lambda item: (
                    item[1],
                    item[0].count(".") + item[0].count("/"),
                    len(item[0]),
                    item[0],
                ),
            )[0]
    if ambiguous_bit_names:
        raise ValueError(
            "Yosys bit同时匹配多个DEF Net，无法无歧义对齐: "
            f"{ambiguous_bit_names[:5]}"
        )

    # 宏单元Pin也可能是非零起始总线；按同一实例的DEF Pin集合恢复正式下标。
    def_pins: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for _, rows in reference_nets.items():
        for instance, pin_name in rows:
            match = re.match(r"^(.*)\[(-?\d+)\]$", pin_name)
            if match:
                def_pins[(instance, canonical_name(match.group(1)))].append(
                    (int(match.group(2)), pin_name)
                )
    for key in list(def_pins):
        def_pins[key] = sorted(
            set(def_pins[key]), key=lambda item: (item[0], item[1])
        )

    gates: dict[str, str] = {}
    nets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    rewritten_gate_names = 0
    unmatched_gate_names: list[str] = []
    for raw_instance, cell in module.get("cells", {}).items():
        raw_instance = canonical_name(raw_instance)
        matched = gate_reference.get(hierarchy_key(raw_instance))
        instance = matched or raw_instance
        if matched and matched != raw_instance:
            rewritten_gate_names += 1
        if not matched:
            unmatched_gate_names.append(raw_instance)
        master = canonical_name(cell.get("type", ""))
        if not master:
            raise ValueError(f"Yosys JSON实例缺少type: {raw_instance}")
        previous = gates.get(instance)
        if previous is not None and previous != master:
            raise ValueError(
                f"Yosys展开后实例重复且master不一致: "
                f"{instance}={previous}/{master}"
            )
        gates[instance] = master

        for raw_pin, raw_bits in cell.get("connections", {}).items():
            pin = canonical_name(raw_pin)
            bits = list(raw_bits)
            reference_pins = def_pins.get((instance, pin), [])
            if len(bits) > 1 and len(reference_pins) == len(bits):
                pin_names = [item[1] for item in reference_pins]
            elif len(bits) > 1:
                pin_names = [f"{pin}[{index}]" for index in range(len(bits))]
            else:
                pin_names = [pin]
            for bit, pin_name in zip(bits, pin_names):
                if isinstance(bit, str):
                    net_name = "CONST1" if bit == "1" else "CONST0"
                else:
                    net_name = bit_names.get(bit, f"BIT_{bit}")
                pair = (instance, pin_name)
                if pair not in nets[net_name]:
                    nets[net_name].append(pair)

    io_rows: list[dict[str, str]] = []
    for raw_name, detail in module.get("ports", {}).items():
        port_name = canonical_name(raw_name)
        bits = list(detail.get("bits", []))
        direction = canonical_name(detail.get("direction", "")).upper()
        port_names = (
            [f"{port_name}[{index}]" for index in range(len(bits))]
            if len(bits) > 1
            else [port_name]
        )
        for bit, bit_port_name in zip(bits, port_names):
            if isinstance(bit, str):
                net_name = "CONST1" if bit == "1" else "CONST0"
            else:
                net_name = bit_names.get(bit, f"BIT_{bit}")
            io_rows.append(
                {
                    "iopin_name": bit_port_name,
                    "net_name": net_name,
                    "direction": direction,
                }
            )

    # r2g-skills delta vs upstream R2G2.0: 上游的main()固定以
    # ``reference_gates={}, reference_nets={}``调用本函数(阶段1按契约不读DEF)，
    # 于是``gate_reference``恒为空、每个Gate都会落进``unmatched_gate_names``，
    # ``unmatched_gate_name_count``恒等于Gate总数。那是一个看起来像告警、实际
    # 恒真的数字。这里显式区分"没有提供参考名"和"提供了但对不上"。
    reference_enabled = bool(reference_gates)
    stats = {
        "parser": "yosys_flattened_json",
        "yosys_bin": str(Path(str(executable)).resolve()),
        "yosys_version_source": str(path),
        "hierarchy_lib_count": len(liberty_paths),
        "json_module_count": len(modules),
        "flattened_gate_count": len(gates),
        "connected_net_count": len(nets),
        "top_level_io_count": len(io_rows),
        "name_reference_enabled": reference_enabled,
        "rewritten_hierarchy_gate_names": rewritten_gate_names,
        "unmatched_gate_name_count": (
            len(unmatched_gate_names) if reference_enabled else 0
        ),
        "unmatched_gate_name_examples": (
            sorted(unmatched_gate_names)[:10] if reference_enabled else []
        ),
        "temporary_json_persisted": False,
    }
    return gates, dict(nets), io_rows, stats


def iter_def_entries(path: Path, section: str):
    """逐条读取 DEF section 中从 ``-`` 到 ``;`` 的完整记录。"""

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


def parse_def_logical(
    path: Path,
    physical_patterns: list[str],
) -> tuple[
    dict[str, str],
    dict[str, list[tuple[str, str]]],
    list[dict[str, str]],
]:
    """解析 DEF 逻辑实例、Net incidence 和顶层 IO。

    物理单元仍参与审计，但不会进入 DEF topology_source 的逻辑 Gate 集合。
    """

    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in physical_patterns]
    all_components: dict[str, str] = {}
    for entry in iter_def_entries(path, "COMPONENTS"):
        parts = entry.split()
        if len(parts) >= 3:
            all_components[canonical_name(parts[1])] = canonical_name(parts[2])
    logical_components = {
        name: master
        for name, master in all_components.items()
        if not any(pattern.search(master) for pattern in patterns)
    }

    nets: dict[str, list[tuple[str, str]]] = {}
    io_rows: list[dict[str, str]] = []
    for entry in iter_def_entries(path, "NETS"):
        match = re.match(r"-\s+(\S+)", entry)
        if not match:
            continue
        net_name = canonical_name(match.group(1))
        connection_text = entry.split("+", 1)[0]
        connections: list[tuple[str, str]] = []
        for left, right in re.findall(r"\(\s*([^\s()]+)\s+([^\s()]+)\s*\)", connection_text):
            left = canonical_name(left)
            right = canonical_name(right)
            if left == "PIN":
                io_rows.append(
                    {"iopin_name": right, "net_name": net_name, "direction": ""}
                )
            elif left in logical_components:
                connections.append((left, right))
        nets[net_name] = list(dict.fromkeys(connections))

    pin_metadata: dict[str, dict[str, str]] = {}
    for entry in iter_def_entries(path, "PINS"):
        name_match = re.match(r"-\s+(\S+)", entry)
        if not name_match:
            continue
        name = canonical_name(name_match.group(1))
        net_match = re.search(r"\+\s*NET\s+(\S+)", entry)
        direction_match = re.search(r"\+\s*DIRECTION\s+(\S+)", entry)
        pin_metadata[name] = {
            "iopin_name": name,
            "net_name": canonical_name(net_match.group(1)) if net_match else "",
            "direction": canonical_name(direction_match.group(1)).upper()
            if direction_match
            else "",
        }
    # PINS section通常比 NETS中的 ``( PIN name )`` 带有更完整的方向信息。
    io_rows = list(pin_metadata.values()) or io_rows
    return logical_components, nets, io_rows


def liberty_bus_members(text: str) -> dict[str, list[int]]:
    """解析Liberty type定义，返回总线类型的正式bit下标。

    fakeram宏使用``bus(rd_out)``配合``type(...){bit_from/bit_to}``描述宽Pin。
    Gate-Pin-Net incidence保存的是``rd_out[95]``这类bit级名称，因此方向表也必须
    展开到相同粒度，不能把整个总线误当成一个Pin。
    """

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


def parse_liberty(
    paths: list[Path],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """解析全部标准单元/宏Liberty的Pin和bit级Bus方向。"""

    directions: dict[str, dict[str, str]] = defaultdict(dict)
    masters: list[str] = []
    for path in paths:
        text = read_liberty_text(path)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        bus_members = liberty_bus_members(text)
        current_cell = ""
        current_port = ""
        current_kind = ""
        current_bus_type = ""
        current_direction = ""
        depth = 0
        cell_depth = -1
        port_depth = -1

        def commit_port() -> None:
            if not current_cell or not current_port or not current_direction:
                return
            cell_directions = directions[current_cell.upper()]
            names = [current_port]
            if current_kind == "bus":
                indices = bus_members.get(current_bus_type)
                if not indices:
                    raise ValueError(
                        f"{path}: bus({current_port})引用未知类型"
                        f"{current_bus_type!r}"
                    )
                names.extend(f"{current_port}[{index}]" for index in indices)
            for name in names:
                previous = cell_directions.get(name)
                if previous and previous != current_direction:
                    raise ValueError(
                        f"Liberty Pin方向冲突: "
                        f"{current_cell}/{name}={previous}/{current_direction}"
                    )
                cell_directions[name] = current_direction

        for raw in text.splitlines():
            line = raw.strip()
            open_count = line.count("{")
            close_count = line.count("}")
            cell_match = re.match(
                r"cell\s*\(\s*\"?([^)\"]+?)\"?\s*\)\s*\{", line
            )
            if cell_match:
                current_cell = canonical_name(cell_match.group(1))
                masters.append(current_cell)
                cell_depth = depth + open_count
                current_port = ""
            port_match = re.match(
                r"(pin|bus)\s*\(\s*\"?([^)\"]+?)\"?\s*\)\s*\{", line
            )
            if current_cell and port_match:
                current_kind = port_match.group(1)
                current_port = canonical_name(port_match.group(2))
                current_bus_type = ""
                current_direction = ""
                port_depth = depth + open_count
            bus_type_match = re.match(
                r"bus_type\s*:\s*\"?([^;\"]+?)\"?\s*;", line
            )
            if current_cell and current_port and bus_type_match:
                current_bus_type = canonical_name(bus_type_match.group(1))
            direction_match = re.match(
                r"direction\s*:\s*\"?([A-Za-z_]+)\"?\s*;", line
            )
            if current_cell and current_port and direction_match:
                current_direction = direction_match.group(1).upper()
            depth += open_count - close_count
            if current_port and depth < port_depth:
                commit_port()
                current_port = ""
                current_kind = ""
                current_bus_type = ""
                current_direction = ""
            if current_cell and depth < cell_depth:
                current_cell = ""
                current_port = ""
    return dict(directions), sorted(set(masters), key=str.upper)


def project_gate_graph(
    gates: dict[str, str],
    nets: dict[str, list[tuple[str, str]]],
    pin_directions: dict[str, dict[str, str]],
) -> tuple[list[tuple[int, int]], list[str], list[str], list[str], dict[str, int]]:
    """将 Net 超边投影成有向 Gate→Gate 边。

    每个已识别 OUTPUT Pin 连接到同一 Net 上的 INPUT Pin。无法识别方向的 Net 仍保留在
    incidence 中，但不猜测有向边，避免人为指定错误 driver。
    """

    gate_names = sorted(gates)
    gate_to_id = {name: index for index, name in enumerate(gate_names)}
    edge_records: list[tuple[int, int, str, str, str]] = []
    stats = {"projected_nets": 0, "no_driver_nets": 0, "no_sink_nets": 0}
    for net_name in sorted(nets):
        drivers: list[tuple[str, str]] = []
        sinks: list[tuple[str, str]] = []
        for instance, pin_name in nets[net_name]:
            master = gates.get(instance, "")
            direction = pin_directions.get(master.upper(), {}).get(pin_name, "")
            if direction in {"OUTPUT", "INOUT", "FEEDTHRU"}:
                drivers.append((instance, pin_name))
            if direction in {"INPUT", "INOUT", "FEEDTHRU"}:
                sinks.append((instance, pin_name))
        if not drivers:
            stats["no_driver_nets"] += 1
        if not sinks:
            stats["no_sink_nets"] += 1
        if drivers and sinks:
            stats["projected_nets"] += 1
        for src_inst, src_pin in drivers:
            for dst_inst, dst_pin in sinks:
                if src_inst == dst_inst:
                    continue
                edge_records.append(
                    (
                        gate_to_id[src_inst],
                        gate_to_id[dst_inst],
                        net_name,
                        src_pin,
                        dst_pin,
                    )
                )
    edge_records = list(dict.fromkeys(edge_records))
    edge_index = [(row[0], row[1]) for row in edge_records]
    return (
        edge_index,
        [row[2] for row in edge_records],
        [row[3] for row in edge_records],
        [row[4] for row in edge_records],
        stats,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="综合网表直接生成 Gate→Gate 基础图")
    parser.add_argument("--config", required=True, help="统一流水线 JSON 配置")
    parser.add_argument("--out", default="", help="覆盖 base_graph.pt 输出路径")
    parser.add_argument(
        "--topology-source",
        choices=("verilog",),
        default="",
        help="逻辑拓扑和顶层IO固定来自综合后Verilog",
    )
    args = parser.parse_args()

    config_path, cfg = load_config(args.config)
    manifest_audit = validate_raw_manifest(config_path, cfg)
    encode_map_path, encoding_maps, encoding_rows = load_encode_maps(
        config_path, cfg
    )
    design_name = str(cfg.get("design_name") or config_path.stem)
    source = args.topology_source or str(cfg.get("topology_source", "verilog"))
    if source != "verilog":
        raise ValueError("阶段1的正式拓扑源必须是综合后Verilog")
    verilog_path = resolve_path(config_path, str(cfg.get("yosys_v", "")), source == "verilog")
    lib_value = cfg.get("lib", "")
    lib_values = lib_value if isinstance(lib_value, list) else [lib_value]
    if not lib_values or not str(lib_values[0]).strip():
        raise ValueError("lib列表不能为空")
    primary_libs: list[Path] = []
    for raw_lib in lib_values:
        resolved_lib = resolve_path(config_path, str(raw_lib), True)
        assert resolved_lib is not None
        primary_libs.append(resolved_lib)
    lib_path = primary_libs[0]
    hierarchy_libs = resolve_yosys_hierarchy_libs(
        config_path, cfg, primary_libs
    )

    assert verilog_path is not None
    gates, nets, io_rows, verilog_parse_stats = (
        parse_synth_verilog_with_yosys(
            verilog_path,
            str(cfg.get("top_module", "")),
            hierarchy_libs,
            str(cfg.get("yosys_bin", "yosys")),
            {},
            {},
        )
    )

    # 顶层IO关系同样来自Yosys JSON。无内部Gate incidence的端口Net仍保留为Net节点。
    verilog_net_count = len(nets)
    io_only_nets = sorted(
        {
            row["net_name"]
            for row in io_rows
            if row.get("net_name") and row["net_name"] not in nets
        }
    )
    for net_name in io_only_nets:
        nets[net_name] = []

    pin_directions, liberty_masters = parse_liberty(hierarchy_libs)
    # r2g-skills delta vs upstream R2G2.0 (D8): fail closed on an empty parse.
    # The missing-cell check below only fires when masters were FOUND, so a
    # Liberty that yields nothing (unreadable, wrong format, gzip decoded as
    # text) sails through it vacuously and the run "succeeds" with every
    # Liberty feature dead and zero gate->gate edges. An empty parse is never a
    # legitimate outcome of a non-empty Liberty set.
    if not liberty_masters:
        raise ValueError(
            "Liberty解析结果为空，拒绝继续: "
            f"{[str(path) for path in hierarchy_libs][:5]}\n"
            "HINT: 确认文件是Liberty (支持.lib/.lib.gz)，且不是被别的格式占位。"
        )
    master_to_id = dict(encoding_maps["cell_type_id"])
    if "UNKNOWN" not in master_to_id:
        raise ValueError("encode_map的cell_type_id缺少UNKNOWN")
    unknown_id = master_to_id["UNKNOWN"]
    missing_liberty_cells = sorted(
        {
            master.upper()
            for master in liberty_masters
            if master.upper() not in master_to_id
        }
    )
    if missing_liberty_cells:
        raise ValueError(
            f"encode_map缺少 {len(missing_liberty_cells)} 个Liberty cell，"
            f"例如 {missing_liberty_cells[:10]}；请先扩展configs/encode_map.csv"
        )
    gate_names = sorted(gates)
    gate_masters = [gates[name] for name in gate_names]
    x = torch.tensor(
        [
            [float(master_to_id.get(master.upper(), unknown_id))]
            for master in gate_masters
        ],
        dtype=torch.float32,
    )
    projected, edge_nets, edge_src_pins, edge_dst_pins, projection_stats = (
        project_gate_graph(gates, nets, pin_directions)
    )
    edge_index = (
        torch.tensor(projected, dtype=torch.long).t().contiguous()
        if projected
        else torch.empty((2, 0), dtype=torch.long)
    )

    # 原始 incidence 使用并行列表保存，阶段2/4按三元组稳定对齐，不依赖任何行号。
    connection_rows = [
        (net_name, instance, pin_name)
        for net_name in sorted(nets)
        for instance, pin_name in nets[net_name]
        if instance in gates
    ]
    net_names = sorted(nets)
    audit: dict[str, Any] = {
        "alignment_source": "none",
        "base_source_only": "post_yosys_verilog",
        "floorplan_read": False,
    }

    data = Data(x=x, edge_index=edge_index)
    data.gate_names = gate_names
    data.gate_masters = gate_masters
    data.net_names = net_names
    data.connection_net_names = [row[0] for row in connection_rows]
    data.connection_inst_names = [row[1] for row in connection_rows]
    data.connection_pin_names = [row[2] for row in connection_rows]
    data.io_pin_names = [row["iopin_name"] for row in io_rows]
    data.io_pin_net_names = [row["net_name"] for row in io_rows]
    data.io_pin_directions = [row["direction"] for row in io_rows]
    data.edge_net_names = edge_nets
    data.edge_src_pin_names = edge_src_pins
    data.edge_dst_pin_names = edge_dst_pins
    data.cell_type_map = master_to_id
    data.encoding_maps = encoding_maps
    data.encode_map_rows = encoding_rows
    data.encode_map_path = str(encode_map_path)
    data.x_schema = ["cell_type_id"]
    data.edge_schema = "driver Gate -> sink Gate; one edge per (src,dst,net)"
    data.design_name = design_name
    data.topology_source = source
    data.data_contract_version = "r2g2_hetero_pipeline_v5"
    data.provenance = {
        "config": str(config_path),
        "yosys_v": str(verilog_path or ""),
        "floorplan_def": "",
        "lib": str(lib_path),
        "yosys_hierarchy_libs": [str(path) for path in hierarchy_libs],
        "encode_map": str(encode_map_path),
    }
    data.verilog_parse_stats = verilog_parse_stats
    data.alignment_audit = audit
    data.projection_stats = projection_stats
    data.manifest_audit = manifest_audit
    data.io_only_net_names = io_only_nets
    data.net_source_stats = {
        "verilog_or_primary_nets": verilog_net_count,
        "floorplan_io_only_nets_added": len(io_only_nets),
    }
    data.net_lineage_contract = {
        "canonical_topology": "base_graph.pt_only",
        "internal_endpoint_key": ["inst_name", "pin_name"],
        "io_endpoint_key": ["iopin_name"],
        "physical_stage_net_policy": (
            "aggregate aligned split/inserted-buffer stage Nets back to the "
            "canonical base Net; never rewrite base topology"
        ),
        "io_only_nets_explicitly_preserved": True,
    }
    data.geometric_edge_contract = {
        "relation": "gate|congestion_geom|gate",
        "base_graph_contains_geometry": False,
        "coordinate_requirement": "trusted_post_placement_or_post_cts_coordinates",
        "disabled_prediction_stages": ["floorplan", "placement"],
        "enabled_prediction_stages": ["cts", "route"],
        "grid_um": 2.1,
        "undirected": True,
        "max_undirected_degree": 5,
        "pyg_storage": "two_symmetric_directed_entries_per_undirected_pair",
    }

    output_dir = resolve_path(
        config_path, str(cfg.get("output_dir", f"output/{design_name}")), False
    )
    assert output_dir is not None
    out_path = (
        Path(args.out).resolve()
        if args.out
        else output_dir / "base_graph" / "base_graph.pt"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_torch_save(data, out_path)

    print(f"[base] saved: {out_path}")
    print(
        f"[base] gates={len(gate_names)} nets={len(net_names)} "
        f"incidences={len(connection_rows)} gate_edges={edge_index.size(1)} ios={len(io_rows)}"
    )
    print(f"[base] source={source} alignment={audit}")
    print(f"[base] projection={projection_stats}")
    if manifest_audit.get("enabled"):
        print(
            f"[base] manifest_verified="
            f"{len(manifest_audit['verified_artifacts'])} "
            f"sample_id={manifest_audit['sample_id']}"
        )


if __name__ == "__main__":
    main()
