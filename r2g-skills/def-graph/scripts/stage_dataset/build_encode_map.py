#!/usr/bin/env python3
"""Generate a platform-specific ``encode_map.csv`` for the R2G2.0 four-stage pipeline.

The vendored upstream ``scripts/r2g2/configs/encode_map.csv`` ships **global**
maps (``cell_function_id``, ``clock_domain_id``, ``net_type_id``,
``orientation_id``, ``pin_direction_id``, ``pin_role_id``, ``pin_type_id``,
``placement_status_id``) plus **nangate45-only** ``cell_type_id`` and
``pin_layer_id`` rows.

Two upstream contracts make that a hard blocker on every other platform:

* ``01_build_base_graph.py`` raises if *any* Liberty cell it parsed is absent
  from ``cell_type_id`` -- there is no UNKNOWN fallback at build time.
* ``02_extract_features.py`` requires the ``pin_layer_id`` map group to exist.

So this generator emits, for one platform:

* every ``technology in {global, *}`` row copied **verbatim** from the upstream
  CSV (ids are upstream's, never renumbered), and
* ``cell_type_id`` / ``pin_layer_id`` rows derived from that platform's Liberty
  and LEF.

Cell enumeration deliberately reuses ``01_build_base_graph.parse_liberty`` --
the same parser, over the same file set that stage 01 will glob -- so "the map
covers every master stage 01 can see" is true by construction rather than by
a second, drifting implementation.

ids are assigned by sorted master name so a regenerated map is byte-stable, and
``UNKNOWN`` always takes the last id. ``cell_type_id`` values are per-platform
categorical: never compare them across platforms.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

R2G2_DIR = Path(__file__).resolve().parent.parent / "r2g2"
UPSTREAM_ENCODE_MAP = R2G2_DIR / "configs" / "encode_map.csv"
FIELDNAMES = [
    "map_name",
    "raw_value",
    "encoded_id",
    "technology",
    "source",
    "physical_meaning",
]
# Same normalization 02_extract_features.normalized_layer_name applies to the
# DEF "+ LAYER <name>" token, so generated raw_values are what lookups use.
_METAL_RE = re.compile(r"(?:M|MET|METAL)(\d+)", re.IGNORECASE)
_VIA_RE = re.compile(r"VIA(\d+)", re.IGNORECASE)


def load_stage_module(filename: str, name: str) -> Any:
    """Import one of the numbered vendored scripts by path.

    They start with a digit, so ``import`` cannot name them; this mirrors the
    ``load_feature_module`` helper upstream uses in stages 03 and 05.
    """

    path = R2G2_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def normalized_layer_name(layer: str) -> str:
    upper = (layer or "").strip().upper()
    match = _METAL_RE.fullmatch(upper)
    if match:
        return f"METAL{int(match.group(1))}"
    match = _VIA_RE.fullmatch(upper)
    if match:
        return f"VIA{int(match.group(1))}"
    return upper or "UNKNOWN"


def read_global_rows(base_csv: Path) -> list[dict[str, str]]:
    """Copy the technology-independent rows verbatim, ids included."""

    with base_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"upstream encode map is empty: {base_csv}")
    kept = [
        {key: row.get(key, "") for key in FIELDNAMES}
        for row in rows
        if row.get("technology", "").strip().lower() in {"global", "*"}
    ]
    if not kept:
        raise ValueError(f"upstream encode map has no global rows: {base_csv}")
    return kept


def liberty_files(lib_dir: Path, extra: list[Path]) -> list[Path]:
    """The exact Liberty set stage 01 will parse.

    ``01.resolve_yosys_hierarchy_libs`` takes the configured ``lib`` list and
    then globs ``*.lib`` beside the first entry, so macro Liberty (fakeram,
    SRAM) is picked up implicitly. Enumerating anything narrower here would let
    stage 01 fail on a master this map never saw.
    """

    paths = list(extra)
    if lib_dir.is_dir():
        paths.extend(sorted(lib_dir.glob("*.lib")))
    unique = list(dict.fromkeys(path.resolve() for path in paths))
    if not unique:
        raise FileNotFoundError(f"no Liberty found for encode map: {lib_dir}")
    return unique


def lef_layer_names(lef_paths: list[Path]) -> list[str]:
    """Routing/cut layer names declared in the tech LEF."""

    names: list[str] = []
    for path in lef_paths:
        if not path.is_file():
            continue
        current = ""
        layer_type = ""
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                tokens = raw.replace(";", " ").split()
                if not tokens:
                    continue
                if tokens[0] == "LAYER" and len(tokens) >= 2:
                    current = tokens[1]
                    layer_type = ""
                elif tokens[0] == "TYPE" and len(tokens) >= 2 and current:
                    layer_type = tokens[1].upper()
                elif tokens[0] == "END" and current:
                    if layer_type in {"ROUTING", "CUT"}:
                        names.append(normalized_layer_name(current))
                    current = ""
                    layer_type = ""
    return names


def build_rows(
    platform: str,
    masters: list[str],
    layers: list[str],
    lib_note: str,
    lef_note: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    ordered_masters = sorted({master.strip().upper() for master in masters if master.strip()})
    for index, master in enumerate(ordered_masters):
        rows.append(
            {
                "map_name": "cell_type_id",
                "raw_value": master,
                "encoded_id": str(index),
                "technology": platform,
                "source": lib_note,
                "physical_meaning": f"Liberty cell {master}",
            }
        )
    rows.append(
        {
            "map_name": "cell_type_id",
            "raw_value": "UNKNOWN",
            "encoded_id": str(len(ordered_masters)),
            "technology": platform,
            "source": lib_note,
            "physical_meaning": "master not present in this platform Liberty",
        }
    )

    ordered_layers = sorted(
        {layer for layer in layers if layer and layer != "UNKNOWN"},
        key=lambda name: (re.sub(r"\d+$", "", name), int(re.search(r"(\d+)$", name).group(1)) if re.search(r"(\d+)$", name) else 0),
    )
    for index, layer in enumerate(ordered_layers):
        rows.append(
            {
                "map_name": "pin_layer_id",
                "raw_value": layer,
                "encoded_id": str(index),
                "technology": platform,
                "source": lef_note,
                "physical_meaning": f"LEF layer {layer}",
            }
        )
    rows.append(
        {
            "map_name": "pin_layer_id",
            "raw_value": "UNKNOWN",
            "encoded_id": str(len(ordered_layers)),
            "technology": platform,
            "source": lef_note,
            "physical_meaning": "layer absent or unrecognized",
        }
    )
    return rows


def write_encode_map(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Emit a platform encode_map.csv for the four-stage pipeline"
    )
    parser.add_argument("--platform", required=True)
    parser.add_argument(
        "--lib-dir",
        default="",
        help="directory globbed for *.lib (defaults to the first --lib's parent)",
    )
    parser.add_argument("--lib", action="append", default=[], help="explicit Liberty file")
    parser.add_argument("--lef", action="append", default=[], help="tech/macro LEF file")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--base",
        default=str(UPSTREAM_ENCODE_MAP),
        help="upstream encode map supplying the global rows",
    )
    args = parser.parse_args()

    explicit_libs = [Path(value).resolve() for value in args.lib if value]
    lib_dir = (
        Path(args.lib_dir).resolve()
        if args.lib_dir
        else (explicit_libs[0].parent if explicit_libs else Path("."))
    )
    libs = liberty_files(lib_dir, explicit_libs)
    base_module = load_stage_module("01_build_base_graph.py", "r2g2_base_for_encode_map")
    _, masters = base_module.parse_liberty(libs)
    if not masters:
        raise SystemExit(f"no Liberty cells parsed from {libs}")

    lefs = [Path(value).resolve() for value in args.lef if value]
    layers = lef_layer_names(lefs)

    rows = read_global_rows(Path(args.base).resolve())
    rows.extend(
        build_rows(
            args.platform.strip().lower(),
            masters,
            layers,
            f"Liberty ({len(libs)} file(s)) under {lib_dir}",
            f"LEF ({len(lefs)} file(s))" if lefs else "no LEF supplied",
        )
    )
    out_path = Path(args.out).resolve()
    write_encode_map(out_path, rows)
    print(
        f"[encode-map] {out_path}: platform={args.platform} "
        f"cells={len(set(m.upper() for m in masters))} layers={len(set(layers))} "
        f"global_rows={sum(1 for r in rows if r['technology'] in {'global', '*'})}"
    )


if __name__ == "__main__":
    main()
