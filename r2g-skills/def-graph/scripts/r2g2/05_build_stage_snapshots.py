#!/usr/bin/env python3
"""生成四个目标阶段完成后的物理/拓扑增量快照。

快照与对应预测图放在同一目录，但属于目标阶段结果审计，不是模型输入：

floorplan: post-Yosys -> Floorplan DEF
placement: Floorplan DEF -> Placement DEF
cts:       Placement DEF -> CTS DEF
route:     CTS DEF -> Route DEF

另生成两张面向Net反标的直达快照：

* snapshots/base_to_placement_snapshot.json：post-Yosys base -> Placement DEF
* snapshots/base_to_route_snapshot.json：post-Yosys base -> Route DEF
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import torch


SCRIPT_DIR = Path(__file__).resolve().parent
STAGE_TRANSITIONS = {
    "floorplan": ("post_yosys", None, "floorplan_def"),
    "placement": ("post_floorplan", "floorplan_def", "place_def"),
    "cts": ("post_placement", "place_def", "cts_def"),
    "route": ("post_cts", "cts_def", "route_def"),
}
MODEL_INPUT_COORDINATE_POLICY = {
    "floorplan": "no_gate_coordinates_post_yosys",
    "placement": "post_floorplan_fixed_macro_and_io_only;standard_cells_untrusted",
    "cts": "post_placement_standard_cell_coordinates_trusted",
    "route": "post_cts_standard_cell_coordinates_trusted",
}
MODEL_INPUT_PHYSICAL_FEATURE_POLICY = {
    stage: {
        "hpwl": stage in {"cts", "route"},
        "congestion_inputs": stage in {"cts", "route"},
        "gate_gate_geometry": stage in {"cts", "route"},
        "all_route_derived_labels_shared": True,
    }
    for stage in ("floorplan", "placement", "cts", "route")
}


def load_feature_module() -> Any:
    path = SCRIPT_DIR / "02_extract_features.py"
    spec = importlib.util.spec_from_file_location("r2g2_four_stage_features", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FEATURES = load_feature_module()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def output_root(config_path: Path, cfg: dict[str, Any]) -> Path:
    path = Path(str(cfg["output_dir"]))
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def source_record(kind: str, path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"kind": kind, "path": "", "sha256": ""}
    return {"kind": kind, "path": str(path), "sha256": sha256_file(path)}


def base_snapshot(base: Any) -> dict[str, Any]:
    components = {
        name: {
            "master": master,
            "status": "",
            "orient": "",
            "x": None,
            "y": None,
        }
        for name, master in zip(base.gate_names, base.gate_masters)
    }
    nets: dict[str, dict[str, Any]] = {
        name: {"connections": [], "use": "", "layers": set()}
        for name in base.net_names
    }
    for net, instance, pin in zip(
        base.connection_net_names,
        base.connection_inst_names,
        base.connection_pin_names,
    ):
        nets.setdefault(net, {"connections": [], "use": "", "layers": set()})
        nets[net]["connections"].append((instance, pin))
    iopins: dict[str, dict[str, Any]] = {}
    for name, net, direction in zip(
        base.io_pin_names,
        base.io_pin_net_names,
        base.io_pin_directions,
    ):
        iopins[name] = {
            "net": net,
            "direction": direction,
            "use": "",
            "layer": "",
            "x": None,
            "y": None,
        }
        nets.setdefault(net, {"connections": [], "use": "", "layers": set()})
        nets[net]["connections"].append(("PIN", name))
    return {
        "dbu": None,
        "die": None,
        "tracks": [],
        "gcell_x": 0,
        "gcell_y": 0,
        "components": components,
        "iopins": iopins,
        "nets": nets,
    }


def endpoint_key(instance: str, pin: str) -> tuple[str, str, str]:
    if instance == "PIN":
        return ("io_pin", "", pin)
    return ("pin", instance, pin)


def endpoint_maps(
    snapshot: dict[str, Any],
) -> tuple[dict[tuple[str, str, str], str], dict[str, set[tuple[str, str, str]]]]:
    endpoint_to_net: dict[tuple[str, str, str], str] = {}
    net_to_endpoints: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    conflicts: list[tuple[Any, str, str]] = []
    for net, info in snapshot["nets"].items():
        for instance, pin in info.get("connections", []):
            key = endpoint_key(str(instance), str(pin))
            previous = endpoint_to_net.get(key)
            if previous is not None and previous != net:
                conflicts.append((key, previous, net))
            endpoint_to_net[key] = net
            net_to_endpoints[net].add(key)
    for name, info in snapshot["iopins"].items():
        net = str(info.get("net", ""))
        if not net:
            continue
        key = ("io_pin", "", name)
        previous = endpoint_to_net.get(key)
        if previous is not None and previous != net:
            conflicts.append((key, previous, net))
        endpoint_to_net[key] = net
        net_to_endpoints[net].add(key)
    if conflicts:
        raise ValueError(f"同一endpoint连接多个net，例如: {conflicts[:10]}")
    return endpoint_to_net, dict(net_to_endpoints)


def component_record(
    name: str, info: dict[str, Any], dbu: float | None
) -> dict[str, Any]:
    position_valid = info.get("x") is not None and info.get("y") is not None
    return {
        "inst_name": name,
        "master": str(info.get("master", "")),
        "placement_status": str(info.get("status", "")),
        "orientation": str(info.get("orient", "")),
        "x_um": float(info["x"]) / dbu if position_valid and dbu else None,
        "y_um": float(info["y"]) / dbu if position_valid and dbu else None,
        "position_valid": bool(position_valid),
    }


def physical_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    dbu = snapshot.get("dbu")
    die = snapshot.get("die")
    die_um = (
        [float(value) / float(dbu) for value in die]
        if die is not None and dbu
        else None
    )
    return {
        "component_count": len(snapshot["components"]),
        "net_count": len(snapshot["nets"]),
        "io_pin_count": len(snapshot["iopins"]),
        "dbu_per_um": dbu,
        "die_um": die_um,
        "track_statement_counts": list(snapshot.get("tracks", [])),
        "gcell_step_dbu": {
            "x": int(snapshot.get("gcell_x", 0)),
            "y": int(snapshot.get("gcell_y", 0)),
        },
    }


def compare_components(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    old = before["components"]
    new = after["components"]
    old_names = set(old)
    new_names = set(new)
    before_dbu = before.get("dbu")
    after_dbu = after.get("dbu")
    added = [
        component_record(name, new[name], after_dbu)
        for name in sorted(new_names - old_names)
    ]
    removed = [
        component_record(name, old[name], before_dbu)
        for name in sorted(old_names - new_names)
    ]
    master_changed: list[dict[str, Any]] = []
    placement_changed: list[dict[str, Any]] = []
    for name in sorted(old_names & new_names):
        old_record = component_record(name, old[name], before_dbu)
        new_record = component_record(name, new[name], after_dbu)
        if old_record["master"].upper() != new_record["master"].upper():
            master_changed.append(
                {
                    "inst_name": name,
                    "before_master": old_record["master"],
                    "after_master": new_record["master"],
                }
            )
        changed_fields = [
            field
            for field in (
                "x_um",
                "y_um",
                "position_valid",
                "orientation",
                "placement_status",
            )
            if old_record[field] != new_record[field]
        ]
        if changed_fields:
            placement_changed.append(
                {
                    "inst_name": name,
                    "changed_fields": changed_fields,
                    "before": {
                        field: old_record[field] for field in changed_fields
                    },
                    "after": {
                        field: new_record[field] for field in changed_fields
                    },
                }
            )
    return {
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "master_changed": len(master_changed),
            "placement_or_orientation_changed": len(placement_changed),
        },
        "added": added,
        "removed": removed,
        "master_changed": master_changed,
        "placement_or_orientation_changed": placement_changed,
    }


def compare_iopins(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    old = before["iopins"]
    new = after["iopins"]
    old_names = set(old)
    new_names = set(new)
    fields = ("net", "direction", "use", "layer", "x", "y")
    changed = []
    for name in sorted(old_names & new_names):
        changed_fields = [
            field
            for field in fields
            if old[name].get(field) != new[name].get(field)
        ]
        if changed_fields:
            changed.append(
                {
                    "iopin_name": name,
                    "changed_fields": changed_fields,
                    "before": {
                        field: old[name].get(field) for field in changed_fields
                    },
                    "after": {
                        field: new[name].get(field) for field in changed_fields
                    },
                }
            )
    return {
        "summary": {
            "added": len(new_names - old_names),
            "removed": len(old_names - new_names),
            "changed": len(changed),
        },
        "added": sorted(new_names - old_names),
        "removed": sorted(old_names - new_names),
        "changed": changed,
    }


def compare_net_lineage(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    before_endpoint, before_net_members = endpoint_maps(before)
    after_endpoint, after_net_members = endpoint_maps(after)
    before_nets = set(before["nets"])
    after_nets = set(after["nets"])
    common_endpoints = set(before_endpoint) & set(after_endpoint)

    forward: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    anchor_counts: dict[tuple[str, str], int] = defaultdict(int)
    for endpoint in common_endpoints:
        old_net = before_endpoint[endpoint]
        new_net = after_endpoint[endpoint]
        forward[old_net].add(new_net)
        reverse[new_net].add(old_net)
        anchor_counts[(old_net, new_net)] += 1

    renamed: list[dict[str, Any]] = []
    split: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unchanged_count = 0
    for old_net in sorted(before_nets):
        targets = sorted(forward.get(old_net, set()))
        if not targets:
            missing.append(
                {
                    "before_net": old_net,
                    "before_endpoint_count": len(
                        before_net_members.get(old_net, set())
                    ),
                }
            )
        elif len(targets) > 1:
            split.append(
                {
                    "before_net": old_net,
                    "after_nets": targets,
                    "after_net_count": len(targets),
                    "anchor_counts": {
                        target: anchor_counts[(old_net, target)]
                        for target in targets
                    },
                }
            )
        elif targets[0] == old_net:
            unchanged_count += 1
        else:
            renamed.append(
                {
                    "before_net": old_net,
                    "after_net": targets[0],
                    "anchor_count": anchor_counts[(old_net, targets[0])],
                }
            )

    merged: list[dict[str, Any]] = []
    backend_only: list[dict[str, Any]] = []
    for new_net in sorted(after_nets):
        sources = sorted(reverse.get(new_net, set()))
        if len(sources) > 1:
            merged.append(
                {
                    "after_net": new_net,
                    "before_nets": sources,
                    "before_net_count": len(sources),
                    "anchor_counts": {
                        source: anchor_counts[(source, new_net)]
                        for source in sources
                    },
                }
            )
        if not sources:
            backend_only.append(
                {
                    "after_net": new_net,
                    "after_endpoint_count": len(
                        after_net_members.get(new_net, set())
                    ),
                }
            )

    return {
        "summary": {
            "before_net_count": len(before_nets),
            "after_net_count": len(after_nets),
            "name_added": len(after_nets - before_nets),
            "name_removed": len(before_nets - after_nets),
            "unchanged": unchanged_count,
            "renamed": len(renamed),
            "split": len(split),
            "merged": len(merged),
            "missing": len(missing),
            "backend_only": len(backend_only),
            "common_endpoint_count": len(common_endpoints),
            "lost_endpoint_count": len(set(before_endpoint) - set(after_endpoint)),
            "new_endpoint_count": len(set(after_endpoint) - set(before_endpoint)),
        },
        "name_added": sorted(after_nets - before_nets),
        "name_removed": sorted(before_nets - after_nets),
        "renamed": renamed,
        "split": split,
        "merged": merged,
        "missing": missing,
        "backend_only": backend_only,
    }


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    path.chmod(0o644)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--output-root", default="")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    root = (
        Path(args.output_root).resolve()
        if args.output_root
        else output_root(config_path, cfg)
    )
    base_path = (
        Path(args.base).resolve()
        if args.base
        else root / "base_graph/base_graph.pt"
    )
    base = torch.load(base_path, map_location="cpu", weights_only=False)
    parsed_cache: dict[str, dict[str, Any]] = {}

    def parsed_source(config_key: str | None) -> tuple[dict[str, Any], Path | None]:
        if config_key is None:
            return base_snapshot(base), None
        if config_key not in parsed_cache:
            source_path = resolve_path(config_path, str(cfg.get(config_key, "")))
            parsed_cache[config_key] = FEATURES.parse_def(source_path)
        return parsed_cache[config_key], resolve_path(
            config_path, str(cfg.get(config_key, ""))
        )

    for stage, (before_kind, before_key, after_key) in STAGE_TRANSITIONS.items():
        before, before_path = parsed_source(before_key)
        after, after_path = parsed_source(after_key)
        assert after_path is not None
        snapshot = {
            "schema": "r2g2_target_stage_snapshot_v1",
            "design_name": str(cfg.get("design_name", config_path.stem)),
            "prediction_stage": stage,
            "snapshot_semantics": (
                "target_stage_completion_delta; audit/auxiliary supervision only; "
                "must_not_be_loaded_as_prediction_input"
            ),
            "model_input_feature_cutoff": {
                "floorplan": "post_yosys",
                "placement": "post_floorplan",
                "cts": "post_placement",
                "route": "post_cts",
            }[stage],
            "model_input_coordinate_policy": MODEL_INPUT_COORDINATE_POLICY[stage],
            "model_input_physical_feature_policy": (
                MODEL_INPUT_PHYSICAL_FEATURE_POLICY[stage]
            ),
            "gate_geom_edge_policy": (
                "disabled_without_trusted_standard_cell_coordinates"
                if stage in {"floorplan", "placement"}
                else "fixed_2p1um_same_grid_undirected_max_degree_5"
            ),
            "transition": {
                "before": source_record(before_kind, before_path),
                "after": source_record(f"post_{stage}", after_path),
            },
            "before_physical_summary": physical_summary(before),
            "after_physical_summary": physical_summary(after),
            "component_delta": compare_components(before, after),
            "io_pin_delta": compare_iopins(before, after),
            "net_lineage": compare_net_lineage(before, after),
            "alignment_key": {
                "internal_pin": ["inst_name", "pin_name"],
                "io_pin": ["iopin_name"],
                "warning": "pin_name alone is not unique",
            },
        }
        out_path = root / "stages" / stage / "stage_snapshot.json"
        atomic_write_json(out_path, snapshot)
        component_summary = snapshot["component_delta"]["summary"]
        net_summary = snapshot["net_lineage"]["summary"]
        print(
            f"[snapshot] {stage}: {out_path}; "
            f"components +{component_summary['added']}/-{component_summary['removed']} "
            f"master_changed={component_summary['master_changed']}; "
            f"nets split={net_summary['split']} merged={net_summary['merged']} "
            f"backend_only={net_summary['backend_only']}"
        )

    # 两张直达快照不描述相邻阶段增量，而是以Canonical base拓扑为锚点，明确列出
    # 每个综合网在Placement/Route中对应的全部小网，供HPWL/线长/RC反标审计。
    direct_specs = {
        "placement": {
            "config_key": "place_def",
            "purpose": (
                "aggregate post-placement segment HPWL as a canonical base-Net "
                "feature"
            ),
        },
        "route": {
            "config_key": "route_def",
            "purpose": (
                "aggregate post-route routed length and RC labels as canonical "
                "base-Net supervision"
            ),
        },
    }
    for physical_stage, spec in direct_specs.items():
        after, after_path = parsed_source(str(spec["config_key"]))
        assert after_path is not None
        lineage = FEATURES.build_base_to_stage_lineage(base, after)
        # 直达快照只保存正向base→stage映射；stage_to_base和direct_stage_nets可由
        # records[*].stage_nets/inferred_backend_stage_nets无损恢复，避免大型设计
        # 在JSON中重复存储数十万次Net名称。
        lineage_for_snapshot = {
            key: value
            for key, value in lineage.items()
            if key != "stage_to_base"
        }
        lineage_for_snapshot["records"] = {
            base_net: {
                key: value
                for key, value in record.items()
                if key != "direct_stage_nets"
            }
            for base_net, record in lineage["records"].items()
        }
        component_delta_summary = compare_components(
            base_snapshot(base), after
        )["summary"]
        direct_snapshot = {
            "schema": "r2g2_base_to_physical_stage_snapshot_v1",
            "design_name": str(cfg.get("design_name", config_path.stem)),
            "physical_stage": physical_stage,
            "snapshot_semantics": (
                "direct canonical-base-to-physical-stage net lineage audit; "
                "not a model input"
            ),
            "coordinate_audit_policy": (
                "raw DEF coordinates are retained in this audit snapshot; model "
                "feature extraction applies its stage-specific trust mask"
            ),
            "purpose": spec["purpose"],
            "transition": {
                "before": source_record("post_yosys_base_graph", base_path),
                "after": source_record(
                    f"post_{physical_stage}", after_path
                ),
            },
            "before_physical_summary": physical_summary(base_snapshot(base)),
            "after_physical_summary": physical_summary(after),
            "component_delta_summary": component_delta_summary,
            "io_pin_delta": compare_iopins(base_snapshot(base), after),
            "net_lineage": lineage_for_snapshot,
            "aggregation_contract": {
                "canonical_topology": "base_graph.pt",
                "physical_segments": "net_lineage.records[*].stage_nets",
                "direct_physical_segments": (
                    "stage_nets minus inferred_backend_stage_nets"
                ),
                "hpwl": "sum of valid per-stage-Net HPWL values",
                "routed_wirelength": (
                    "sum of Route DEF Manhattan lengths over stage_nets"
                ),
                "ground_capacitance": (
                    "sum of SPEF ground capacitance over stage_nets"
                ),
                "ambiguous_policy": (
                    "do not aggregate added-component components anchored to "
                    "multiple canonical base Nets"
                ),
            },
            "alignment_key": {
                "internal_pin": ["inst_name", "pin_name"],
                "io_pin": ["iopin_name"],
                "warning": "pin_name alone is not unique",
            },
        }
        out_path = (
            root
            / "snapshots"
            / f"base_to_{physical_stage}_snapshot.json"
        )
        atomic_write_json(out_path, direct_snapshot)
        summary = lineage["summary"]
        print(
            f"[snapshot] base->{physical_stage}: {out_path}; "
            f"split={summary['split_base_net_count']} "
            f"inferred_backend_nets="
            f"{summary['inferred_backend_stage_net_count']} "
            f"unaligned={summary['unaligned_base_net_count']}"
        )


if __name__ == "__main__":
    main()
