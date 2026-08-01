#!/usr/bin/env python3
"""Turn one ORFS backend run into an R2G2.0 four-stage sample config.

Upstream R2G2.0 expects a hand-written JSON pointing at a curated
``/data/R2G2_dynamic_dataset/...`` tree that does not exist here. Everything it
needs, though, is already inside an ORFS run directory -- just as ``.odb``
stage snapshots rather than DEF:

    ORFS ``results/``                     R2G2.0 config field
    ------------------------------------  ---------------------------------
    1_2_yosys.v                           yosys_v      (canonical topology)
    2_floorplan.odb  -> 2_floorplan.def   floorplan_def  (placement input)
    3_place.odb      -> 3_place.def       place_def      (cts input)
    4_cts.odb        -> 4_cts.def         cts_def        (route input)
    5_route.odb      -> 5_route.def       route_def / label_def  (labels only)
    6_final.spef                          spef           (RC labels)
    6_final.sdc                           sdc
    <platform lib/lef via resolve.py>     lib / lef

This adapter writes the stage DEFs, the raw ``manifest.json`` whose SHA256s and
stage ``semantics`` tokens the upstream integrity gates check, a per-platform
``encode_map.csv``, and the sample config itself.

Two things are deliberately *derived* rather than assumed:

* ``top_module`` is read out of the synthesized netlist rather than trusted from
  ``run-meta.json``; ORFS ``DESIGN_NAME`` and the Verilog top can differ, and
  stage 01 hard-fails on a wrong top.
* ``congestion_grid_pitch_um`` comes from the platform tech LEF's third routing
  layer. Upstream hardcodes Nangate45's 0.14 um Metal3 pitch in three files; on
  sky130/gf180/ihp that constant describes another technology's grid.

The stage-input whitelist is the point of the whole dataset, so this adapter
never writes ``5_route.def`` into a feature field -- it appears only as
``route_def``/``label_def``, and ``checks/validate_four_stage.py`` re-checks that
independently.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
FLOW_DIR = SKILL_DIR / "scripts" / "flow"
R2G2_DIR = SKILL_DIR / "scripts" / "r2g2"
ODB_TO_DEF = SKILL_DIR / "scripts" / "extract" / "graph" / "odb_to_def.py"
BUILD_ENCODE_MAP = Path(__file__).resolve().parent / "build_encode_map.py"

# stage key -> (odb basename, def basename, manifest artifact, semantics)
# The semantics strings are not decoration: validate_manifest_stage() requires
# the artifact's semantics to contain the *input cutoff* token for the stage it
# feeds ("floorplan" feeds placement, "placement" feeds cts, "cts" feeds route),
# and validate_label_stage() requires "routing" on the label DEF.
STAGE_ARTIFACTS = [
    ("floorplan_def", "2_floorplan.odb", "2_floorplan.def", "floorplan_def", "post_floorplan_snapshot"),
    ("place_def", "3_place.odb", "3_place.def", "placement_def", "post_placement_snapshot"),
    ("cts_def", "4_cts.odb", "4_cts.def", "cts_def", "post_cts_snapshot"),
    ("route_def", "5_route.odb", "5_route.def", "routing_def", "post_routing_snapshot"),
]
YOSYS_NETLIST_CANDIDATES = ("1_2_yosys.v", "1_1_yosys.v", "1_synth.v")
CONGESTION_GRID_TRACKS = 15
NANGATE45_METAL3_PITCH_UM = 0.14


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_checked(command: list[str], what: str) -> str:
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-3000:]
        raise SystemExit(f"{what} failed (rc={result.returncode}):\n{detail}")
    return result.stdout


def resolve_platform_paths(config_mk: Path | None, platform: str) -> dict[str, str]:
    """KEY=VALUE contract from the shared resolver (same one run_features.sh uses)."""

    script = FLOW_DIR / "resolve_platform_paths.sh"
    stdout = run_checked(
        ["bash", str(script), str(config_mk or ""), platform],
        f"resolve_platform_paths.sh {platform}",
    )
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line and not line.startswith(" "):
            key, _, value = line.partition("=")
            if key.strip().isupper():
                values[key.strip()] = value.strip()
    for required in ("LIB_FILES", "TECH_LEF"):
        if not values.get(required):
            raise SystemExit(
                f"platform {platform}: resolver produced no {required}\n{stdout}"
            )
    return values


def split_paths(value: str) -> list[Path]:
    return [Path(token) for token in value.split() if token.strip()]


def detect_top_module(netlist: Path) -> str:
    """Last top-level ``module`` declared in the synthesized netlist.

    Yosys writes the top module last in the flattened netlist; taking the last
    declaration avoids picking a leaf blackbox stub that some flows emit first.
    ``hierarchy -check -top`` in stage 01 will reject a wrong guess loudly, so a
    wrong answer here fails closed rather than silently mis-rooting the graph.
    """

    names: list[str] = []
    pattern = re.compile(r"^\s*module\s+\\?([A-Za-z_][\w$.\\\[\]]*)")
    with netlist.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = pattern.match(line)
            if match:
                names.append(match.group(1).replace("\\", ""))
    if not names:
        raise SystemExit(f"no module declaration found in {netlist}")
    return names[-1]


def third_routing_layer_pitch_um(tech_lef: Path) -> float | None:
    """Pitch of the third routing layer, i.e. the FastRoute congestion pitch.

    Returns ``None`` when the tech LEF exposes fewer than three routing layers,
    in which case the caller keeps the Nangate45 default and says so.
    """

    if not tech_lef.is_file():
        return None
    layers: list[tuple[str, float]] = []
    current = ""
    block: dict[str, Any] = {}
    with tech_lef.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            tokens = raw.replace(";", " ").split()
            if not tokens:
                continue
            if tokens[0] == "LAYER" and len(tokens) >= 2:
                current = tokens[1]
                block = {"type": "", "pitch": []}
            elif not current:
                continue
            elif tokens[0] == "TYPE" and len(tokens) >= 2:
                block["type"] = tokens[1].upper()
            elif tokens[0] == "PITCH":
                for token in tokens[1:]:
                    try:
                        block["pitch"].append(float(token))
                    except ValueError:
                        pass
            elif tokens[0] == "END":
                if block.get("type") == "ROUTING" and block.get("pitch"):
                    layers.append((current, max(block["pitch"])))
                current = ""
                block = {}
    if len(layers) < 3:
        return None
    return layers[2][1]


def export_stage_defs(
    results_dir: Path, out_dir: Path, orfs_root: str, force: bool
) -> dict[str, Path]:
    """ODB -> DEF for the four stage snapshots (one OpenROAD call per stage)."""

    out_dir.mkdir(parents=True, exist_ok=True)
    produced: dict[str, Path] = {}
    pending: list[tuple[Path, Path]] = []
    for field, odb_name, def_name, _, _ in STAGE_ARTIFACTS:
        odb = results_dir / odb_name
        target = out_dir / def_name
        if not odb.is_file():
            raise SystemExit(
                f"missing stage snapshot {odb}\n"
                "HINT: the four-stage dataset needs 2_floorplan/3_place/4_cts/"
                "5_route .odb from one completed ORFS run."
            )
        produced[field] = target
        if force or not target.is_file() or target.stat().st_mtime < odb.stat().st_mtime:
            pending.append((odb, target))
    for odb, target in pending:
        print(f"[stage-def] {odb.name} -> {target}", flush=True)
        run_checked(
            [
                sys.executable,
                str(ODB_TO_DEF),
                str(odb),
                "--def",
                str(target),
                "--orfs-root",
                orfs_root,
            ],
            f"odb_to_def {odb.name}",
        )
    if not pending:
        print("[stage-def] all four stage DEFs already current", flush=True)
    return produced


def write_manifest(
    manifest_path: Path,
    *,
    sample_id: str,
    top_module: str,
    platform: str,
    netlist: Path,
    stage_defs: dict[str, Path],
    spef: Path,
) -> None:
    artifacts: dict[str, Any] = {
        "yosys_netlist": {
            "path": os.path.relpath(netlist, manifest_path.parent),
            "sha256": sha256_file(netlist),
            "semantics": "post_synthesis_netlist",
        }
    }
    for field, _, _, artifact_name, semantics in STAGE_ARTIFACTS:
        path = stage_defs[field]
        artifacts[artifact_name] = {
            "path": os.path.relpath(path, manifest_path.parent),
            "sha256": sha256_file(path),
            "semantics": semantics,
        }
    artifacts["final_spef"] = {
        "path": os.path.relpath(spef, manifest_path.parent),
        "sha256": sha256_file(spef),
        "semantics": "post_route_parasitics",
    }
    payload = {
        "schema": "r2g2_raw_sample_manifest_v1",
        "generator": "def-graph/scripts/stage_dataset/make_sample_config.py",
        "sample_id": sample_id,
        "top_module": top_module,
        "platform": platform,
        "artifacts": artifacts,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(manifest_path)
    finally:
        temporary.unlink(missing_ok=True)


TIMING_CONFIG_FIELDS = (
    "timing_max_rpt",
    "timing_min_rpt",
    "timing_manifest",
    "timing_require_manifest",
    "timing_enabled",
)


def carry_over_timing_fields(previous: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """Preserve timing sources across a config regeneration.

    ``emit_timing_reports.py --update-config`` writes the ``timing_*`` fields
    back into the sample config. Rewriting the config from scratch would reset
    ``timing_enabled`` to False while ``labels/pin_timing.csv`` stays on disk --
    the assembled graphs would then carry all-NaN slack next to a populated CSV,
    which reads as "this design has no timing" rather than "the config was
    regenerated". Carry the fields over only while the reports they name still
    exist, so a genuinely missing report still degrades the column honestly.

    Returns True when fields were carried over.
    """

    carried = {key: previous[key] for key in TIMING_CONFIG_FIELDS if key in previous}
    if not carried:
        return False
    if not all(
        Path(str(carried.get(key, ""))).is_file()
        for key in ("timing_max_rpt", "timing_min_rpt", "timing_manifest")
    ):
        return False
    cfg.update(carried)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ORFS backend run -> R2G2.0 four-stage sample config"
    )
    parser.add_argument("--run-dir", required=True, help="…/backend/RUN_<tag>")
    parser.add_argument("--out-dir", required=True, help="four-stage dataset root")
    parser.add_argument("--platform", default="", help="override run-meta platform")
    parser.add_argument("--design-name", default="", help="override run-meta design_name")
    parser.add_argument("--config-mk", default="", help="override ORFS config.mk")
    parser.add_argument("--sdc", default="", help="override SDC (default results/6_final.sdc)")
    parser.add_argument("--encode-map", default="", help="reuse an existing encode_map.csv")
    parser.add_argument("--force", action="store_true", help="re-export stage DEFs")
    parser.add_argument(
        "--congestion-grid-um",
        type=float,
        default=None,
        help="override the fixed congestion GCell size (um)",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    results = run_dir / "results"
    if not results.is_dir():
        raise SystemExit(f"not an ORFS run dir (no results/): {run_dir}")
    meta_path = run_dir / "run-meta.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    platform = (args.platform or str(meta.get("platform", ""))).strip()
    if not platform:
        raise SystemExit("platform unknown: pass --platform or provide run-meta.json")
    design_name = (args.design_name or str(meta.get("design_name", "")) or run_dir.parent.parent.name).strip()

    netlist = next((results / name for name in YOSYS_NETLIST_CANDIDATES if (results / name).is_file()), None)
    if netlist is None:
        raise SystemExit(
            f"no synthesized netlist in {results} "
            f"(looked for {', '.join(YOSYS_NETLIST_CANDIDATES)})"
        )
    spef = results / "6_final.spef"
    if not spef.is_file():
        raise SystemExit(f"missing {spef}; RC labels need post-route SPEF")
    sdc = Path(args.sdc).resolve() if args.sdc else results / "6_final.sdc"

    # run-meta.json records the config.mk path as it was at run time, which may
    # point at a corpus root that has since moved; prefer the design's own copy.
    config_mk_candidates = [
        Path(args.config_mk) if args.config_mk else None,
        run_dir.parent.parent / "constraints" / "config.mk",
        Path(str(meta.get("config_mk", ""))) if meta.get("config_mk") else None,
    ]
    config_mk = next((p for p in config_mk_candidates if p and p.is_file()), None)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stage_def_dir = out_dir / "stage_defs"

    paths = resolve_platform_paths(config_mk, platform)
    libs = split_paths(paths.get("LIB_FILES", "")) + split_paths(paths.get("ADDITIONAL_LIBS", ""))
    lefs = split_paths(paths.get("TECH_LEF", ""))
    lefs += split_paths(paths.get("SC_LEF", "")) + split_paths(paths.get("ADDITIONAL_LEFS", ""))
    libs = [p for p in dict.fromkeys(libs) if p.is_file()]
    lefs = [p for p in dict.fromkeys(lefs) if p.is_file()]
    if not libs or not lefs:
        raise SystemExit(f"platform {platform}: no usable lib/lef ({paths})")

    orfs_root = os.environ.get("ORFS_ROOT", "")
    stage_defs = export_stage_defs(results, stage_def_dir, orfs_root, args.force)
    top_module = detect_top_module(netlist)

    encode_map = Path(args.encode_map).resolve() if args.encode_map else out_dir / "encode_map.csv"
    if not args.encode_map:
        command = [sys.executable, str(BUILD_ENCODE_MAP), "--platform", platform, "--out", str(encode_map)]
        for lib in libs:
            command += ["--lib", str(lib)]
        for lef in lefs:
            command += ["--lef", str(lef)]
        print(run_checked(command, "build_encode_map.py").strip(), flush=True)

    if args.congestion_grid_um is not None:
        grid_um: float | None = float(args.congestion_grid_um)
        pitch_um: float | None = None
        grid_source = "operator_override"
    else:
        pitch_um = third_routing_layer_pitch_um(lefs[0])
        grid_um = None
        grid_source = (
            f"tech_lef_third_routing_layer_pitch({pitch_um}um)x{CONGESTION_GRID_TRACKS}"
            if pitch_um
            else f"nangate45_default_metal3_pitch({NANGATE45_METAL3_PITCH_UM}um)x{CONGESTION_GRID_TRACKS}"
        )

    manifest_path = out_dir / "manifest.json"
    write_manifest(
        manifest_path,
        sample_id=design_name,
        top_module=top_module,
        platform=platform,
        netlist=netlist,
        stage_defs=stage_defs,
        spef=spef,
    )

    cfg: dict[str, Any] = {
        "schema": "r2g2_four_stage_sample_v1",
        "design_name": design_name,
        "sample_id": design_name,
        "design_family": design_name,
        "version": run_dir.name,
        "top_module": top_module,
        "platform": platform,
        "topology_source": "verilog",
        "orfs_run_dir": str(run_dir),
        "raw_manifest": str(manifest_path),
        "encode_map": str(encode_map),
        "yosys_v": str(netlist),
        "floorplan_def": str(stage_defs["floorplan_def"]),
        "place_def": str(stage_defs["place_def"]),
        "cts_def": str(stage_defs["cts_def"]),
        "route_def": str(stage_defs["route_def"]),
        "label_def": str(stage_defs["route_def"]),
        "spef": str(spef),
        "lib": [str(p) for p in libs],
        "lef": [str(p) for p in lefs],
        "yosys_hierarchy_lib_dir": str(libs[0].parent),
        "feature_profile": "pre_route",
        "rc_source_mode": "spef",
        "congestion_grid_tracks": CONGESTION_GRID_TRACKS,
        "congestion_grid_source": grid_source,
        "output_dir": str(out_dir / "generated"),
    }
    if sdc.is_file():
        cfg["sdc"] = str(sdc)
    if config_mk is not None:
        cfg["config_mk"] = str(config_mk)
    if grid_um is not None:
        cfg["congestion_grid_um"] = grid_um
    elif pitch_um:
        cfg["congestion_grid_pitch_um"] = pitch_um
    else:
        cfg["congestion_grid_pitch_um"] = NANGATE45_METAL3_PITCH_UM
    # timing/IR labels are attached by the runner once their sources exist;
    # absent sources must degrade one label column, never fabricate values.
    cfg["timing_enabled"] = False

    config_path = out_dir / f"{design_name}.json"
    # Regenerating the config must not silently downgrade an existing dataset:
    # emit_timing_reports.py writes timing_* back here, and blindly resetting
    # timing_enabled to False would leave stale pin_timing.csv on disk while the
    # assembled graphs carry all-NaN slack. Carry the fields over when the
    # reports they name are still present (and drop them when they are not).
    if config_path.is_file():
        try:
            previous = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if carry_over_timing_fields(previous, cfg):
            print("[config] carried over existing timing_* sources", flush=True)

    temporary = config_path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(config_path)
    finally:
        temporary.unlink(missing_ok=True)

    effective_grid = cfg.get("congestion_grid_um") or (
        CONGESTION_GRID_TRACKS * float(cfg["congestion_grid_pitch_um"])
    )
    print(f"[config] {config_path}")
    print(f"[config] design={design_name} top={top_module} platform={platform}")
    print(f"[config] congestion grid={effective_grid:g}um source={grid_source}")
    print(f"[config] manifest={manifest_path} encode_map={encode_map}")


if __name__ == "__main__":
    main()
