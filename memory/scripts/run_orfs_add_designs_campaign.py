#!/usr/bin/env python3
"""Bounded campaign: add genuinely-new RTL designs to the training lineage.

Goal (2026-08-02): grow training RTL lineage diversity so the frozen held-out
SPI lineage's nearest graph-context distance falls inside the OOD ceiling (3.0),
then (once passing) add a second frozen held-out lineage for external review.

New training designs (aes / ibex / jpeg — architecturally distinct from the
existing gcd/riscv32i training and the frozen spi held-out) run on the three
platforms where the held-out distances were measured (sky130hs / sky130hd /
ihp-sg13g2). Each (design, platform) materializes a base + a DENSITY_RELIEF
after, producing one verified transition + physical effect with a def-graph
context. Eligible observations land only in a campaign-local staging store;
canonical import is a separate lifecycle-authority operation.

Reuses the diversity-campaign helpers (materialize / run / capture / graph).
Firewall: spi is never a training lineage here.
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

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import (  # noqa: E402
    _load,
    _materialize,
    _write,
    attach_graph_contexts,
    capture_pairs,
    preflight_orfs_toolchain,
    run_projects,
)
from run_orfs_batch0 import (  # noqa: E402
    _bind_sdc,
    run_equivalence,
    run_graph_contexts,
    run_signoff,
)

from tehm import db as tehm_db  # noqa: E402
from tehm.batch_lane import (  # noqa: E402
    BatchLaneError,
    _input_binding,
    _timing_contract,
)
from tehm.adapters.semantic_oracle import load_spec  # noqa: E402
from orfs_storage import default_work_root, enforce_work_root, storage_policy  # noqa: E402

VERSION = "orfs-add-designs-v0.1"
SOURCE_FREEZE_VERSION = "orfs-add-designs-source-freeze-v1"

# Per-index knob schedules (mirror the v3-contexts campaign's scheme so new
# training contexts are comparable to the strata already in the store).
CORE_UTILS = (30, 35, 40)
ROUTE_AFTER = ("0.05", "0.10", "0.15")
PLACE_AFTER = ("0.50", "0.55", "0.60")

# family -> (knob, after-project slug, before_value_fn(index), after_value_fn(index)).
# The before arm is always the shared base project; the after arm applies the
# family knob on top of it. ROUTING/PLACEMENT leave the base at ORFS defaults,
# so their before_value is the honest label "default" (no knob value is set).
# PLACE_DENSITY is mutually exclusive with PLACE_DENSITY_LB_ADDON (util.tcl
# prefers LB_ADDON), so the PLACEMENT after edit deletes LB_ADDON via None.
FAMILY_SPECS = {
    "DENSITY_RELIEF": ("CORE_UTILIZATION", "density",
                       lambda i: str(CORE_UTILS[i]),
                       lambda i: str(CORE_UTILS[i] - 10)),
    "ROUTING_CAPACITY_RECOVERY": ("ROUTING_LAYER_ADJUSTMENT", "routing",
                                  lambda i: "default",
                                  lambda i: ROUTE_AFTER[i]),
    "PLACEMENT_DENSITY_RECOVERY": ("PLACE_DENSITY", "placement",
                                   lambda i: "default",
                                   lambda i: PLACE_AFTER[i]),
}
_FAMILY_AFTER_EDITS = {
    "DENSITY_RELIEF": lambda knob, value, i: {knob: value},
    "ROUTING_CAPACITY_RECOVERY": lambda knob, value, i: {knob: value},
    "PLACEMENT_DENSITY_RECOVERY": lambda knob, value, i: {
        knob: value, "PLACE_DENSITY_LB_ADDON": None},
}
DEFAULT_FAMILIES = tuple(FAMILY_SPECS)

# New designs genuinely absent from training AND != the frozen spi held-out.
# aes fails ABC on sky130hd; ibex_sv is SystemVerilog (ORFS Yosys rejects it);
# uart/fifo have no per-platform templates so they ride a platform template
# with a VERILOG_FILES override (the same mechanism the held-out SPI uses).
DEFAULT_DESIGNS = ("jpeg", "uart", "fifo")
# Platforms where the held-out SPI nearest distances were measured
# (sky130hs 3.66-4.65, sky130hd 7.4-7.85, ihp-sg13g2 431.7). ihp is skipped in
# the bounded batch: aes/jpeg time out there and the 431 gap is not bridged by
# a handful of contexts.
DEFAULT_PLATFORMS = ("sky130hs", "sky130hd")

# src-only designs -> the platform-template design whose config.mk we reuse.
# RTL comes from flow/designs/src/<design>/*.v (the held-out SPI mechanism).
_SRC_ONLY_DESIGNS = {
    "uart": "gcd",
    "fifo": "gcd",
}


def _template_auxiliary_edits(
        cfg_template: Path, template: Path, *, logical_top_changed: bool) -> dict[str, str]:
    """Bind template-owned auxiliary paths when the logical RTL top changes.

    Source-only/custom-RTL campaigns reuse a platform design template but set
    ``DESIGN_NAME`` to the source-bound top.  A template such as nangate45/gcd
    expresses its PDN path through ``$(DESIGN_NAME)``; without rebinding, the
    materialized project asks ORFS for ``flow/designs/<new-top>/grid_*.tcl``
    even though the file belongs to the reused gcd template.  Resolve only
    paths that explicitly depend on the changed top and require the concrete
    template file to exist.  Native designs retain their original config
    semantics unchanged.
    """
    if not logical_top_changed:
        return {}
    try:
        text = cfg_template.read_text(errors="replace")
    except OSError as exc:
        raise BatchLaneError(f"template config unreadable: {cfg_template}") from exc
    match = re.search(
        r"(?m)^\s*(?:override\s+)?(?:export\s+)?PDN_TCL\s*[:?]?=\s*(.*?)\s*$",
        text)
    if not match:
        return {}
    raw = match.group(1).strip().rstrip("\\").strip()
    if "$(DESIGN_NAME)" not in raw and "$(DESIGN_NICKNAME)" not in raw:
        return {}
    candidate = (template / Path(raw.split()[-1]).name).resolve()
    if not candidate.is_file():
        raise BatchLaneError(
            f"template auxiliary PDN_TCL is missing for source-bound design: {candidate}")
    return {"PDN_TCL": str(candidate)}


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _stable(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str)


def _git_output(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True,
            text=True, check=False)
    except OSError as exc:
        return f"UNAVAILABLE:{exc}"
    return proc.stdout if proc.returncode == 0 else f"RC={proc.returncode}:{proc.stderr}"


def _rtl_files(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        return []
    return sorted(
        (path.resolve() for path in source_dir.iterdir()
         if path.is_file() and path.suffix.lower() in {".v", ".sv"}),
        key=str)


_MODULE_RE = re.compile(r"(?m)^\s*module\s+([A-Za-z_]\w*)\b")
_INSTANCE_RE = re.compile(
    r"\b([A-Za-z_]\w*)\s*(?:#\s*\([^;]*?\)\s*)?"
    r"[A-Za-z_]\w*\s*\(")


def _infer_rtl_top(rtl_paths: list[Path], *, design: str,
                   preferred: str | None = None) -> str:
    """Infer the RTL top and fail closed on an ambiguous custom source.

    A custom RTL override is a new source lineage, so silently using the
    template's top is unsafe.  Single-module fixtures are unambiguous; for a
    multi-module source choose the module that is not instantiated by another
    declared module and reject ties rather than guessing.
    """
    modules: list[str] = []
    instantiated: set[str] = set()
    for path in rtl_paths:
        text = path.read_text(errors="replace")
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        text = re.sub(r"//.*", " ", text)
        modules.extend(_MODULE_RE.findall(text))
        # Remove ordinary module declarations before scanning type/name calls;
        # this keeps ``module child(...)`` from looking like an instance while
        # still handling a child instantiation on the same line as its parent.
        body = re.sub(r"\bmodule\s+[A-Za-z_]\w*\s*(?:#\s*\([^;]*?\)\s*)?\(",
                      " ", text)
        instantiated.update(_INSTANCE_RE.findall(body))
    unique = list(dict.fromkeys(modules))
    if not unique:
        raise BatchLaneError(f"cannot infer RTL top for {design}: no module declaration")
    if preferred and preferred in unique:
        return preferred
    if len(unique) == 1:
        return unique[0]
    candidates = [name for name in unique if name not in instantiated]
    if len(candidates) == 1:
        return candidates[0]
    raise BatchLaneError(
        f"ambiguous RTL top for {design}: modules={unique}; pass a single-top RTL override")


def _sdc_clock_port(path: Path) -> str:
    text = path.read_text(errors="replace")
    matches = re.findall(r"(?m)^\s*set\s+clk_port_name\s+([^\s#]+)", text)
    if len(matches) != 1 or not matches[0]:
        raise BatchLaneError(
            f"custom SDC must declare exactly one 'set clk_port_name <port>': {path}")
    return matches[0]


def _file_record(path: Path) -> dict:
    path = Path(path).resolve()
    try:
        size = path.stat().st_size
        exists = path.is_file()
    except OSError:
        size, exists = None, False
    return {"path": str(path), "exists": exists,
            "sha256": _sha(path) if exists else None, "bytes": size}


def _campaign_inputs(orfs_root: Path, *, designs, platforms,
                     rtl_override_path: Path | None,
                     sdc_override_path: Path | None,
                     template_design: str,
                     semantic_oracle_path: Path | None = None) -> list[dict]:
    """Resolve the exact config/SDC/RTL inputs before materialization.

    The record is intentionally independent of generated project directories:
    a later prepare/observe step can recompute it and reject any source drift
    instead of treating a changed input as the same experiment.
    """
    rows = []
    for platform in platforms:
        for design in designs:
            src_template = (template_design if rtl_override_path is not None
                            else _SRC_ONLY_DESIGNS.get(design))
            platform_design = src_template if src_template else design
            template = (Path(orfs_root) / "flow" / "designs" /
                        platform / platform_design)
            cfg = template / "config.mk"
            sdc = (Path(sdc_override_path).resolve()
                   if sdc_override_path is not None
                   else template / "constraint.sdc")
            rtl = ([Path(rtl_override_path).resolve()]
                   if rtl_override_path is not None else
                   _rtl_files(Path(orfs_root) / "flow" / "designs" /
                              "src" / design))
            auxiliary_edits = _template_auxiliary_edits(
                cfg, template,
                logical_top_changed=(rtl_override_path is not None or
                                      bool(_SRC_ONLY_DESIGNS.get(design))))
            row = {
                "platform": str(platform), "design": str(design),
                "template_design": str(platform_design),
                "config": _file_record(cfg), "sdc": _file_record(sdc),
                "rtl": [_file_record(path) for path in rtl],
            }
            if auxiliary_edits:
                row["auxiliary"] = [
                    {"key": key, "file": _file_record(Path(value))}
                    for key, value in sorted(auxiliary_edits.items())
                ]
            if semantic_oracle_path is not None:
                row["semantic_oracle"] = _file_record(semantic_oracle_path)
            rows.append(row)
    return rows


def _source_code_records() -> list[dict]:
    roots = [MEMORY_ROOT / "tehm"]
    explicit = [
        MEMORY_ROOT / "schema.sql",
        MEMORY_ROOT / "requirements.txt",
        Path(__file__).resolve(),
        MEMORY_ROOT / "scripts" / "run_orfs_diversity_campaign.py",
        MEMORY_ROOT / "scripts" / "orfs_storage.py",
    ]
    paths = list(explicit)
    for base in roots:
        paths.extend(path for path in base.rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts)
    return [_file_record(path) for path in sorted(set(paths), key=str)]


def _freeze_request(*, designs, platforms, families, indexes: int,
                    core_utils, lineage_prefix: str,
                    rtl_override_path: Path | None,
                    sdc_override_path: Path | None,
                    template_design: str, orfs_root: Path,
                    dataset_split: str = "training",
                    semantic_oracle_path: Path | None = None) -> dict:
    if dataset_split not in {"training", "calibration", "heldout", "ab"}:
        raise ValueError(f"invalid dataset split: {dataset_split!r}")
    request = {
        "orfs_root": str(Path(orfs_root).resolve()),
        "designs": [str(value) for value in designs],
        "platforms": [str(value) for value in platforms],
        "families": [str(value) for value in families],
        "indexes": int(indexes),
        "core_utils": [int(value) for value in core_utils],
        "lineage_prefix": str(lineage_prefix),
        "template_design": str(template_design),
        "rtl_override": (str(Path(rtl_override_path).resolve())
                          if rtl_override_path is not None else None),
        "sdc_override": (str(Path(sdc_override_path).resolve())
                          if sdc_override_path is not None else None),
    }
    # Preserve compatibility with already-frozen training campaigns while
    # binding non-training capture roles into new source freezes.  A held-out
    # transfer run must not be reclassified by editing only the manifest.
    if dataset_split != "training":
        request["dataset_split"] = dataset_split
    if semantic_oracle_path is not None:
        # Validate before writing the freeze, then bind the exact file path in
        # the request and its digest via ``_campaign_inputs``.
        load_spec(semantic_oracle_path)
        request["semantic_oracle"] = str(Path(semantic_oracle_path).resolve())
    return request


def build_source_freeze(root: Path, orfs_root: Path, *, designs, platforms,
                        families, indexes: int, core_utils,
                        lineage_prefix: str, rtl_override_path: Path | None,
                        sdc_override_path: Path | None,
                        template_design: str,
                        dataset_split: str = "training",
                        semantic_oracle_path: Path | None = None) -> dict:
    """Freeze campaign inputs before any project is materialized or run."""
    request = _freeze_request(
        designs=designs, platforms=platforms, families=families,
        indexes=indexes, core_utils=core_utils, lineage_prefix=lineage_prefix,
        rtl_override_path=rtl_override_path,
        sdc_override_path=sdc_override_path, template_design=template_design,
        orfs_root=orfs_root, dataset_split=dataset_split,
        semantic_oracle_path=semantic_oracle_path)
    inputs = _campaign_inputs(
        Path(orfs_root).resolve(), designs=designs, platforms=platforms,
        rtl_override_path=rtl_override_path,
        sdc_override_path=sdc_override_path, template_design=template_design,
        semantic_oracle_path=semantic_oracle_path)
    toolchain_request = {"orfs_root": request["orfs_root"]}
    if os.environ.get("R2G_TOOLCHAIN_MANIFEST"):
        toolchain_request["toolchain_manifest"] = os.environ["R2G_TOOLCHAIN_MANIFEST"]
    toolchain = preflight_orfs_toolchain(toolchain_request)
    source_code = _source_code_records()
    freeze = {
        "version": SOURCE_FREEZE_VERSION,
        "git_head": _git_output("rev-parse", "HEAD").strip(),
        "git_status_sha256": hashlib.sha256(
            _git_output("status", "--porcelain=v1").encode()).hexdigest(),
        "request": request,
        "source_code": source_code,
        "source_tree_digest": hashlib.sha256(_stable(source_code).encode()).hexdigest(),
        "inputs": inputs,
        "input_digest": hashlib.sha256(_stable(inputs).encode()).hexdigest(),
        "toolchain": toolchain,
    }
    freeze["freeze_digest"] = hashlib.sha256(
        _stable(freeze).encode()).hexdigest()
    _write(Path(root) / "source_freeze.json", freeze)
    return freeze


def _validate_source_freeze(path: Path, *, orfs_root: Path, designs,
                            platforms, families, indexes: int, core_utils,
                            lineage_prefix: str, rtl_override_path: Path | None,
                            sdc_override_path: Path | None,
                            template_design: str,
                            dataset_split: str = "training",
                            semantic_oracle_path: Path | None = None) -> dict:
    path = Path(path).resolve()
    try:
        freeze = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchLaneError(
            "source freeze is required and must be valid JSON; run --phase freeze first: "
            f"{path}") from exc
    if not isinstance(freeze, dict) or freeze.get("version") != SOURCE_FREEZE_VERSION:
        raise BatchLaneError(f"source freeze version mismatch: {path}")
    digest = freeze.get("freeze_digest")
    unsigned = dict(freeze)
    unsigned.pop("freeze_digest", None)
    if not digest or hashlib.sha256(_stable(unsigned).encode()).hexdigest() != digest:
        raise BatchLaneError("source freeze digest mismatch; rebuild the freeze")
    expected_request = _freeze_request(
        designs=designs, platforms=platforms, families=families,
        indexes=indexes, core_utils=core_utils, lineage_prefix=lineage_prefix,
        rtl_override_path=rtl_override_path,
        sdc_override_path=sdc_override_path, template_design=template_design,
        orfs_root=orfs_root, dataset_split=dataset_split,
        semantic_oracle_path=semantic_oracle_path)
    if freeze.get("request") != expected_request:
        raise BatchLaneError(
            "source freeze request mismatch; use the same campaign arguments "
            "or run --phase freeze again")
    current_inputs = _campaign_inputs(
        Path(orfs_root).resolve(), designs=designs, platforms=platforms,
        rtl_override_path=rtl_override_path,
        sdc_override_path=sdc_override_path, template_design=template_design,
        semantic_oracle_path=semantic_oracle_path)
    if (freeze.get("input_digest") !=
            hashlib.sha256(_stable(current_inputs).encode()).hexdigest()):
        raise BatchLaneError(
            "source freeze input digest mismatch; config/SDC/RTL drifted after freeze")
    current_source = _source_code_records()
    if (freeze.get("source_tree_digest") !=
            hashlib.sha256(_stable(current_source).encode()).hexdigest()):
        raise BatchLaneError(
            "source freeze code digest mismatch; rebuild the freeze before prepare")
    frozen_toolchain = freeze.get("toolchain") or {}
    toolchain_request = {"orfs_root": str(Path(orfs_root).resolve())}
    locked_manifest = frozen_toolchain.get("toolchain_manifest")
    if locked_manifest:
        toolchain_request["toolchain_manifest"] = str(locked_manifest)
    current_toolchain = preflight_orfs_toolchain(toolchain_request)
    if current_toolchain.get("fingerprint") != frozen_toolchain.get("fingerprint"):
        raise BatchLaneError(
            "source freeze toolchain fingerprint mismatch; rerun --phase freeze")
    return freeze


def _validate_manifest_source_freeze(manifest: dict) -> dict:
    """Revalidate an already-prepared campaign before any later phase."""
    raw_path = manifest.get("source_freeze")
    if not raw_path:
        raise BatchLaneError(
            "campaign manifest has no source freeze; rerun --phase freeze then prepare")
    path = Path(str(raw_path)).expanduser().resolve()
    if manifest.get("source_freeze_sha256") != _sha(path):
        raise BatchLaneError("campaign source freeze file changed after prepare")
    try:
        freeze = json.loads(path.read_text())
        request = freeze["request"]
        if not isinstance(request, dict):
            raise TypeError("request is not an object")
        required = {
            "orfs_root", "designs", "platforms", "families", "indexes",
            "core_utils", "lineage_prefix", "template_design",
        }
        if not required.issubset(request):
            raise KeyError("request fields are incomplete")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BatchLaneError("campaign source freeze is malformed") from exc
    if manifest.get("source_freeze_digest") != freeze.get("freeze_digest"):
        raise BatchLaneError("campaign manifest/source-freeze digest mismatch")
    _validate_source_freeze(
        path, orfs_root=Path(request["orfs_root"]),
        designs=tuple(request["designs"]), platforms=tuple(request["platforms"]),
        families=tuple(request["families"]), indexes=int(request["indexes"]),
        core_utils=tuple(request["core_utils"]),
        lineage_prefix=str(request["lineage_prefix"]),
        rtl_override_path=(Path(request["rtl_override"])
                          if request.get("rtl_override") else None),
        sdc_override_path=(Path(request["sdc_override"])
                          if request.get("sdc_override") else None),
        template_design=str(request["template_design"]),
        dataset_split=str(request.get("dataset_split", "training")),
        semantic_oracle_path=(Path(request["semantic_oracle"])
                              if request.get("semantic_oracle") else None),
    )
    return freeze


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path,
                    default=default_work_root("orfs-v4-add-designs"))
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--staging-db", type=Path, default=None,
                    help="required isolated destination; defaults below <root>/staging")
    ap.add_argument("--staging-artifacts", type=Path, default=None)
    ap.add_argument("--designs", nargs="+", default=list(DEFAULT_DESIGNS))
    ap.add_argument("--platforms", nargs="+", default=list(DEFAULT_PLATFORMS))
    ap.add_argument("--rtl-override", type=Path, default=None,
                    help="custom RTL file used for every requested design; the design is a new lineage")
    ap.add_argument("--sdc-override", type=Path, default=None,
                    help="SDC template paired with --rtl-override")
    ap.add_argument("--semantic-oracle", type=Path, default=None,
                    help=("source-frozen JSON semantic failure contract; evaluated "
                          "from each materialized config, never caller booleans"))
    ap.add_argument("--template-design", default="gcd",
                    help="platform design template for --rtl-override (default: gcd)")
    ap.add_argument("--families", nargs="+", default=list(DEFAULT_FAMILIES),
                    choices=list(FAMILY_SPECS))
    ap.add_argument("--indexes", type=int, default=1,
                    help="core-utilization indices per (design, platform); 1 = bounded")
    ap.add_argument("--core-utils", nargs="+", type=int, default=list(CORE_UTILS),
                    help="base CORE_UTILIZATION schedule (for example 20 25 30)")
    ap.add_argument("--lineage-prefix", default="orfs-v4",
                    help="prefix for training lineage IDs in this campaign")
    ap.add_argument("--dataset-split", choices=("training", "calibration", "heldout", "ab"),
                    default="training",
                    help=("capture role for this bounded campaign; non-training "
                          "roles remain audit-only and cannot become learner support"))
    ap.add_argument("--phase", choices=(
        "all", "freeze", "prepare", "run", "equivalence", "signoff",
        "graph", "capture", "report"),
                    default="all")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--cpus-per-run", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--supervisor-grace", type=int, default=90)
    ap.add_argument("--projects", nargs="+", default=None,
                    help="optional absolute project paths to run")
    args = ap.parse_args(argv)
    if args.rtl_override and not args.sdc_override:
        ap.error("--rtl-override requires --sdc-override so current_design/clock provenance is explicit")
    root = enforce_work_root(args.root)
    root.mkdir(parents=True, exist_ok=True)
    staging_db = (args.staging_db or root / "staging" / "tehm.sqlite").resolve()
    staging_artifacts = (args.staging_artifacts or root / "staging" / "artifacts").resolve()
    manifest_path = root / "campaign_manifest.json"
    orfs_root = args.orfs_root.resolve()
    freeze_path = root / "source_freeze.json"

    if args.phase in ("all", "freeze"):
        build_source_freeze(
            root, orfs_root, designs=tuple(args.designs),
            platforms=tuple(args.platforms), families=tuple(args.families),
            indexes=args.indexes, core_utils=tuple(args.core_utils),
            lineage_prefix=args.lineage_prefix,
            rtl_override_path=(args.rtl_override.resolve()
                               if args.rtl_override else None),
            sdc_override_path=(args.sdc_override.resolve()
                               if args.sdc_override else None),
            template_design=args.template_design,
            dataset_split=args.dataset_split,
            semantic_oracle_path=(args.semantic_oracle.resolve()
                                  if args.semantic_oracle else None))
        if args.phase == "freeze":
            return 0

    if args.phase in ("all", "prepare"):
        manifest = prepare(root, orfs_root,
                           designs=tuple(args.designs),
                           platforms=tuple(args.platforms),
                           families=tuple(args.families),
                           indexes=args.indexes, core_utils=tuple(args.core_utils),
                           lineage_prefix=args.lineage_prefix,
                           rtl_override_path=(args.rtl_override.resolve()
                                              if args.rtl_override else None),
                           sdc_override_path=(args.sdc_override.resolve()
                                              if args.sdc_override else None),
                           template_design=args.template_design,
                           dataset_split=args.dataset_split,
                           semantic_oracle_path=(args.semantic_oracle.resolve()
                                                 if args.semantic_oracle else None),
                           source_freeze=freeze_path)
    else:
        manifest = _load(manifest_path)
        if not manifest:
            raise RuntimeError(f"manifest missing: {manifest_path}")
        _validate_manifest_source_freeze(manifest)
    if args.phase == "prepare":
        return 0
    allowlist = ({str(Path(p).resolve()) for p in args.projects}
                 if args.projects else None)
    if args.phase in ("all", "run"):
        run_projects(root, manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=args.timeout,
                     supervisor_grace=max(1, args.supervisor_grace),
                     project_allowlist=allowlist)
    if args.phase == "run":
        return 0
    if args.phase in ("all", "equivalence"):
        run_equivalence(root, manifest, timeout=args.timeout,
                        project_allowlist=allowlist)
    if args.phase == "equivalence":
        return 0
    if args.phase in ("all", "signoff"):
        run_signoff(root, manifest, timeout=args.timeout,
                    project_allowlist=allowlist)
    if args.phase == "signoff":
        return 0
    if args.phase in ("all", "graph"):
        # The batch-0 graph phase emits the persisted graph receipt consumed by
        # assess_full_oracle().  attach_graph_contexts below then binds the
        # resulting digest to the staging physical effect.
        run_graph_contexts(root, manifest, project_allowlist=allowlist)
    if args.phase == "graph":
        if manifest.get("captured"):
            attach_graph_contexts(root, manifest, staging_db)
        return 0
    if args.phase in ("all", "capture"):
        capture_pairs(manifest_path, manifest, staging_db, staging_artifacts,
                      dataset_campaign_id=VERSION,
                      require_complete_oracle=True,
                      require_full_oracle=True,
                      default_dataset_split=manifest.get("dataset_split"))
        manifest = _load(manifest_path)
    if args.phase == "capture":
        return 0
    if args.phase == "all":
        attach_graph_contexts(root, manifest, staging_db)
    if args.phase in ("all", "report"):
        report(root, manifest, staging_db)
    return 0


def prepare(root: Path, orfs_root: Path, *, designs, platforms,
            families, indexes: int = 1, core_utils=CORE_UTILS,
            lineage_prefix: str = "orfs-v4", rtl_override_path: Path | None = None,
            sdc_override_path: Path | None = None,
            template_design: str = "gcd",
            dataset_split: str = "training",
            semantic_oracle_path: Path | None = None,
            source_freeze: Path | None = None) -> dict:
    core_utils = tuple(int(x) for x in core_utils)
    if not core_utils:
        raise ValueError("core_utils must contain at least one utilization")
    if source_freeze is None:
        raise BatchLaneError(
            "source freeze is required before prepare; run --phase freeze first")
    frozen = _validate_source_freeze(
        source_freeze, orfs_root=orfs_root, designs=designs,
        platforms=platforms, families=families, indexes=indexes,
        core_utils=core_utils, lineage_prefix=lineage_prefix,
        rtl_override_path=rtl_override_path,
        sdc_override_path=sdc_override_path, template_design=template_design,
        dataset_split=dataset_split,
        semantic_oracle_path=semantic_oracle_path)
    semantic_oracle = (load_spec(semantic_oracle_path)
                       if semantic_oracle_path is not None else None)
    family_specs = {
        "DENSITY_RELIEF": ("CORE_UTILIZATION", "density",
                           lambda i: str(core_utils[i]),
                           lambda i: str(core_utils[i] - 10)),
        "ROUTING_CAPACITY_RECOVERY": FAMILY_SPECS["ROUTING_CAPACITY_RECOVERY"],
        "PLACEMENT_DENSITY_RECOVERY": FAMILY_SPECS["PLACEMENT_DENSITY_RECOVERY"],
    }
    custom_top = None
    custom_clock_port = None
    if rtl_override_path is not None:
        custom_top = _infer_rtl_top([rtl_override_path.resolve()], design=str(designs[0]))
        custom_clock_port = _sdc_clock_port(sdc_override_path.resolve())
    items, baselines = [], []
    for platform in platforms:
        for design in designs:
            src_template = (template_design if rtl_override_path is not None
                            else _SRC_ONLY_DESIGNS.get(design))
            platform_design = src_template if src_template else design
            template = orfs_root / "flow" / "designs" / platform / platform_design
            cfg = template / "config.mk"
            sdc = (sdc_override_path if sdc_override_path is not None
                   else template / "constraint.sdc")
            if not cfg.is_file() or not sdc.is_file():
                print(f"[prepare] SKIP {platform}/{design}: template incomplete "
                      f"({template})", file=sys.stderr)
                continue
            rtl_override = None
            rtl_paths: list[Path] = []
            if rtl_override_path is not None:
                if not rtl_override_path.is_file():
                    raise FileNotFoundError(f"custom RTL override missing: {rtl_override_path}")
                rtl_override = str(rtl_override_path)
                rtl_paths = [rtl_override_path.resolve()]
            elif src_template:
                src_dir = orfs_root / "flow" / "designs" / "src" / design
                src_rtl = _rtl_files(src_dir)
                if not src_rtl:
                    print(f"[prepare] SKIP {platform}/{design}: no src RTL in "
                          f"{src_dir}", file=sys.stderr)
                    continue
                rtl_override = " ".join(str(p.resolve()) for p in src_rtl)
                rtl_paths = src_rtl
            else:
                rtl_paths = _rtl_files(
                    orfs_root / "flow" / "designs" / "src" / design)
            for index in range(indexes):
                util = core_utils[index % len(core_utils)]
                common = {"CORE_UTILIZATION": str(util),
                          "PLACE_DENSITY_LB_ADDON": "0.25"}
                common.update(_template_auxiliary_edits(
                    cfg, template,
                    logical_top_changed=(rtl_override_path is not None or
                                          bool(src_template))))
                if rtl_override:
                    # The logical campaign label may intentionally differ
                    # from the Verilog module name (for example ``fifo``
                    # backed by ``selector_fifo16.v``).  ORFS synth must use
                    # the source-bound top inferred above; retaining the
                    # label here produces a deterministic synth failure before
                    # any oracle evidence can be collected.
                    common["DESIGN_NAME"] = custom_top or design
                    common["VERILOG_FILES"] = rtl_override
                    # Source-only designs must retain positive equivalence.
                    # An expensive or unproven EQY result is external evidence,
                    # never a reason to weaken the semantic admission gate.
                    common["EQUIVALENCE_CHECK"] = "1"
                base = _materialize(root / "cases" / f"{platform}_{design}_base_{index}",
                                    cfg, sdc, dict(common))
                if custom_top is not None:
                    _bind_sdc(base / "constraints" / "constraint.sdc",
                              top=custom_top, clock_port=custom_clock_port)
                baselines.append({"baseline_id": f"{platform}:{design}:base{index}",
                                  "platform": platform, "design": design,
                                  "index": index, "project": str(base)})
                for family in families:
                    knob, slug, before_fn, after_fn = family_specs[family]
                    before_value = before_fn(index)
                    after_value = after_fn(index)
                    after_edits = _FAMILY_AFTER_EDITS[family](knob, after_value, index)
                    after = _materialize(
                        root / "cases" / f"{platform}_{design}_{slug}_{index}",
                        cfg, sdc, {**common, **after_edits})
                    if custom_top is not None:
                        _bind_sdc(after / "constraints" / "constraint.sdc",
                                  top=custom_top, clock_port=custom_clock_port)
                    before_binding = _input_binding(base, rtl_paths)
                    after_binding = _input_binding(after, rtl_paths)
                    items.append({
                        "case_id": f"{platform}:{design}:{index}:{family}:"
                                   f"{before_value}->{after_value}",
                        "lineage_id": f"{lineage_prefix}:{platform}:{design}:base{index}",
                        "platform": platform, "design": design, "family": family,
                        "check": "route", "knob": knob,
                        "before_value": before_value, "after_value": after_value,
                        "config_edits": {knob: after_value},
                        "before_project": str(base), "after_project": str(after),
                        "top": custom_top or _infer_rtl_top(
                            rtl_paths, design=str(design), preferred=str(design)),
                        "source_digest": before_binding.get("source_digest"),
                        "rtl_files": [str(path) for path in rtl_paths],
                        "input_bindings": {
                            "before": before_binding,
                            "after": after_binding,
                        },
                        "timing_contract": {
                            "before": _timing_contract(base),
                            "after": _timing_contract(after),
                        },
                        "semantic_oracle": semantic_oracle,
                        "role": dataset_split,
                        "dataset_split": dataset_split,
                    })

    if not items:
        raise RuntimeError("no materialized items; check ORFS templates")
    frozen_toolchain = frozen.get("toolchain") or {}
    heldout = {"lineage_id": "orfs-heldout-v3:ihp-sg13g2:spi",
               "platform": "ihp-sg13g2", "design": "spi",
               "role": "calibration_only", "capturable": False}
    manifest = {
        "campaign_version": VERSION, "lineage_prefix": lineage_prefix,
        "dataset_split": dataset_split,
        "semantic_oracle": semantic_oracle,
        "semantic_oracle_path": (str(Path(semantic_oracle_path).resolve())
                                  if semantic_oracle_path is not None else None),
        "semantic_oracle_sha256": (_sha(semantic_oracle_path)
                                    if semantic_oracle_path is not None else None),
        "orfs_root": str(orfs_root),
        "source_freeze": str(Path(source_freeze).resolve()),
        "source_freeze_sha256": _sha(source_freeze),
        "source_freeze_digest": frozen.get("freeze_digest"),
        "toolchain_manifest": frozen_toolchain.get("toolchain_manifest"),
        "items": items, "baselines": baselines, "core_utils": list(core_utils),
        "storage_policy": storage_policy(root),
        "families": list(families), "heldout": heldout,
        "firewall": {
            "training_lineages": sorted({x["lineage_id"] for x in items
                                           if dataset_split == "training"}),
            "heldout_lineages": ([heldout["lineage_id"]] if dataset_split != "heldout"
                                  else sorted({x["lineage_id"] for x in items})),
            "disjoint": heldout["lineage_id"] not in {x["lineage_id"] for x in items},
            # spi must NEVER be a training lineage here.
            "spi_absent_from_training": "spi" not in {x["design"] for x in items},
        },
    }
    _write(root / "campaign_manifest.json", manifest)
    return manifest


def report(root: Path, manifest: dict, db_path: Path) -> dict:
    conn = tehm_db.connect(db_path)
    n_trans = conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]
    n_effects = conn.execute("SELECT COUNT(*) FROM tehm_physical_effects").fetchone()[0]
    unique_ctx = conn.execute(
        "SELECT COUNT(DISTINCT graph_context_digest) FROM tehm_physical_effects "
        "WHERE graph_context_digest IS NOT NULL AND graph_context_digest != ''").fetchone()[0]
    conn.close()
    captured = list(manifest.get("captured", []))
    learner_rows = [row for row in captured if row.get("learner_eligible") is True]
    incomplete_rows = [row for row in captured
                       if row.get("oracle_complete") is not True]
    result = {
        "campaign_version": VERSION,
        "source_freeze": manifest.get("source_freeze"),
        "source_freeze_sha256": manifest.get("source_freeze_sha256"),
        "source_freeze_digest": manifest.get("source_freeze_digest"),
        "captured": len(captured),
        "oracle_complete": len(captured) - len(incomplete_rows),
        "incomplete_oracle": len(incomplete_rows),
        "learner_eligible": len(learner_rows),
        "canonical_transition_total": n_trans,
        "physical_effect_total": n_effects,
        "unique_graph_contexts": unique_ctx,
        "firewall": manifest["firewall"],
    }
    _write(root / "add_designs_report.json", result)
    print(f"[add-designs] transitions={n_trans} effects={n_effects} "
          f"unique_contexts={unique_ctx}", flush=True)
    return result


if __name__ == "__main__":
    sys.exit(main())
