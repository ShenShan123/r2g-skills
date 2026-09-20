"""Isolated fixed-flow project staging for TEHM Research Runtime RC1.

This module materializes only adapter-bound inputs.  It does not execute EDA,
infer missing files, generate stubs, or grant flow/repair authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from tehm.evaluation.research_inventory import verify_research_inventory


STAGE_RECEIPT_SCHEMA = "tehm-research-flow-stage-v1"
STAGE_ARTIFACT_SCHEMA = "tehm-research-flow-stage-artifacts-v1"


class ResearchFlowError(ValueError):
    """Raised when fixed-flow inputs cannot be staged or verified."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_bytes(_canonical(value) + b"\n")
    temporary.replace(path)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchFlowError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchFlowError(f"{label} must be a JSON object")
    return value


def _relative(value: object, label: str) -> PurePosixPath:
    relative = PurePosixPath(str(value or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ResearchFlowError(f"{label} is unsafe")
    return relative


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _set_make_assignment(text: str, key: str, value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ResearchFlowError(f"make assignment {key} contains a newline")
    pattern = re.compile(
        rf"(?m)^[ \t]*(?:export[ \t]+)?{re.escape(key)}[ \t]*[:?+]?=.*$"
    )
    replacement = f"export {key} = {value}"
    if pattern.search(text):
        return pattern.sub(replacement, text)
    suffix = "" if text.endswith("\n") else "\n"
    return text + suffix + replacement + "\n"


def _artifact_manifest(root: Path, paths: list[Path]) -> dict[str, Any]:
    rows = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        if not path.is_file() or path.is_symlink():
            raise ResearchFlowError(f"staged input is missing or a symlink: {path}")
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        })
    payload = {
        "schema": STAGE_ARTIFACT_SCHEMA,
        "files": rows,
        "files_digest": _digest(rows),
    }
    payload["manifest_digest"] = _digest(payload)
    return payload


def _design(inventory_root: Path, design_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    inventory = _load_json(inventory_root / "inventory.json", "research inventory")
    index = {
        row.get("design_id"): row for row in inventory.get("designs") or []
        if isinstance(row, Mapping)
    }
    item = index.get(design_id)
    if not isinstance(item, Mapping):
        raise ResearchFlowError(f"design is absent from inventory: {design_id}")
    relative = _relative(item.get("manifest_path"), "design manifest path")
    manifest = _load_json(inventory_root / Path(*relative.parts), "design manifest")
    if manifest.get("manifest_digest") != item.get("manifest_digest"):
        raise ResearchFlowError("inventory design binding mismatch")
    return inventory, manifest


def stage_flow_project(
    *, inventory: str | Path, design_id: str, output: str | Path,
) -> dict[str, Any]:
    """Create one immutable-input project for the existing ORFS runner."""
    inventory_root = Path(inventory).expanduser().resolve()
    checked = verify_research_inventory(inventory_root)
    destination = Path(output).expanduser().resolve()
    source_inventory, manifest = _design(inventory_root, design_id)
    corpus = Path(str(source_inventory["corpus_root"])).resolve()
    if destination.exists():
        raise ResearchFlowError(f"refusing to overwrite staged project: {destination}")
    if _inside(destination, corpus):
        raise ResearchFlowError("staged project must be outside the source corpus")
    if any(character.isspace() for character in str(destination)):
        raise ResearchFlowError("staged project path cannot contain whitespace")
    adapter = manifest.get("adapter_binding")
    if not isinstance(adapter, Mapping):
        raise ResearchFlowError("design has no explicit adapter binding")
    if adapter.get("logic_changes") != [] or adapter.get("stub_generated") is not False:
        raise ResearchFlowError("flow staging forbids logic changes and generated stubs")
    flow = adapter.get("flow_binding")
    if not isinstance(flow, Mapping):
        raise ResearchFlowError("design has no flow binding")
    platform = flow.get("platform")
    if type(platform) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]+", platform):
        raise ResearchFlowError("flow platform is invalid")
    compilation = manifest.get("compilation") or {}
    if compilation.get("top_authority") != "explicit_adapter_spec":
        raise ResearchFlowError("flow staging requires an explicit adapter top")
    if compilation.get("filelist_authority") != "explicit:adapter-spec.json":
        raise ResearchFlowError("flow staging requires an explicit adapter filelist")
    top = compilation.get("top_module")
    if type(top) is not str or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", top):
        raise ResearchFlowError("flow top module is invalid")

    support_index = {
        row.get("source_path"): row for row in adapter.get("support_files") or []
        if isinstance(row, Mapping)
    }
    config_support = support_index.get(flow.get("config_template"))
    sdc_support = support_index.get(flow.get("sdc_template"))
    if not isinstance(config_support, Mapping) or not isinstance(sdc_support, Mapping):
        raise ResearchFlowError("flow config or SDC is not frozen in the adapter")
    frozen_config = inventory_root / Path(*_relative(
        config_support.get("frozen_path"), "frozen config path"
    ).parts)
    frozen_sdc = inventory_root / Path(*_relative(
        sdc_support.get("frozen_path"), "frozen SDC path"
    ).parts)
    for path, record, label in (
        (frozen_config, config_support, "config"),
        (frozen_sdc, sdc_support, "SDC"),
    ):
        if (not path.is_file() or path.stat().st_size != record.get("bytes")
                or _sha256_file(path) != record.get("sha256")):
            raise ResearchFlowError(f"frozen adapter {label} drifted")

    source_project = corpus / Path(*_relative(
        manifest.get("project_path"), "source project path"
    ).parts)
    source_entries = {
        row.get("path"): row for row in manifest.get("source_files") or []
        if isinstance(row, Mapping)
    }
    filelist = compilation.get("ordered_filelist")
    if not isinstance(filelist, list) or not filelist:
        raise ResearchFlowError("explicit flow filelist is empty")
    staging = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    if staging.exists():
        raise ResearchFlowError(f"flow staging path already exists: {staging}")
    staged_paths: list[Path] = []
    copied_sources = []
    staging.mkdir(parents=True)
    try:
        for value in filelist:
            relative = _relative(value, "ordered source path")
            record = source_entries.get(relative.as_posix())
            if not isinstance(record, Mapping):
                raise ResearchFlowError(f"ordered source is not source-bound: {relative}")
            source = source_project / Path(*relative.parts)
            if (not source.is_file() or source.is_symlink()
                    or source.stat().st_size != record.get("bytes")
                    or _sha256_file(source) != record.get("sha256")):
                raise ResearchFlowError(f"source input drifted: {relative}")
            target = staging / "rtl" / Path(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            staged_paths.append(target)
            copied_sources.append({
                "source_path": relative.as_posix(),
                "staged_path": (Path("rtl") / Path(*relative.parts)).as_posix(),
                "sha256": record["sha256"],
                "bytes": record["bytes"],
            })

        constraints = staging / "constraints"
        constraints.mkdir(parents=True)
        staged_sdc = constraints / "constraint.sdc"
        shutil.copyfile(frozen_sdc, staged_sdc)
        staged_paths.append(staged_sdc)
        final_rtl = [destination / row["staged_path"] for row in copied_sources]
        final_include_dirs = []
        for value in compilation.get("include_dirs") or []:
            relative = _relative(value, "include directory")
            final_include_dirs.append(str(destination / "rtl" / Path(*relative.parts)))
        config_text = frozen_config.read_text(encoding="utf-8")
        config_text = _set_make_assignment(config_text, "DESIGN_NAME", top)
        config_text = _set_make_assignment(config_text, "DESIGN_NICKNAME", design_id)
        config_text = _set_make_assignment(config_text, "PLATFORM", platform)
        config_text = _set_make_assignment(
            config_text, "VERILOG_FILES", " ".join(str(path) for path in final_rtl)
        )
        config_text = _set_make_assignment(
            config_text, "VERILOG_INCLUDE_DIRS", " ".join(final_include_dirs)
        )
        config_text = _set_make_assignment(
            config_text, "SDC_FILE", str(destination / "constraints/constraint.sdc")
        )
        reserved = {"DESIGN_NAME", "DESIGN_NICKNAME", "PLATFORM",
                    "VERILOG_FILES", "VERILOG_INCLUDE_DIRS", "SDC_FILE"}
        overrides = flow.get("overrides") or {}
        if not isinstance(overrides, Mapping):
            raise ResearchFlowError("flow overrides must be an object")
        for key, value in sorted(overrides.items()):
            if type(key) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                raise ResearchFlowError("flow override name is invalid")
            if key in reserved:
                continue
            config_text = _set_make_assignment(config_text, key, str(value))
        staged_config = constraints / "config.mk"
        staged_config.write_text(config_text, encoding="utf-8")
        staged_paths.append(staged_config)

        receipt = {
            "schema": STAGE_RECEIPT_SCHEMA,
            "design_id": design_id,
            "adapter_id": adapter.get("adapter_id"),
            "inventory_path": str(inventory_root),
            "inventory_digest": checked["inventory_digest"],
            "design_manifest_digest": manifest["manifest_digest"],
            "source_bundle_digest": manifest["source_bundle_digest"],
            "authority_checkout": dict(adapter.get("authority_checkout") or {}),
            "platform": platform,
            "top_module": top,
            "source_files": copied_sources,
            "config_template": {
                "source_path": config_support["source_path"],
                "sha256": config_support["sha256"],
            },
            "sdc_template": {
                "source_path": sdc_support["source_path"],
                "sha256": sdc_support["sha256"],
            },
            "declared_overrides": dict(overrides),
            "canonical_staged_assignments": {
                "DESIGN_NAME": top,
                "DESIGN_NICKNAME": design_id,
                "PLATFORM": platform,
                "VERILOG_FILES": [str(path) for path in final_rtl],
                "VERILOG_INCLUDE_DIRS": final_include_dirs,
                "SDC_FILE": str(destination / "constraints/constraint.sdc"),
            },
            "logic_changes": [],
            "stub_generated": False,
            "flow_executed": False,
            "authority": "staged inputs only; no flow or repair readiness granted",
        }
        receipt["receipt_digest"] = _digest(receipt)
        receipt_path = staging / "stage-receipt.json"
        _write_json(receipt_path, receipt)
        staged_paths.append(receipt_path)
        _write_json(
            staging / "staged-input-manifest.json",
            _artifact_manifest(staging, staged_paths),
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_staged_flow_project(destination)


def verify_staged_flow_project(path: str | Path) -> dict[str, Any]:
    """Verify immutable staged inputs while allowing later output directories."""
    root = Path(path).expanduser().resolve()
    receipt = _load_json(root / "stage-receipt.json", "flow stage receipt")
    artifacts = _load_json(root / "staged-input-manifest.json", "staged input manifest")
    if receipt.get("schema") != STAGE_RECEIPT_SCHEMA:
        raise ResearchFlowError("flow stage receipt schema mismatch")
    unsigned = dict(receipt)
    claimed_receipt = unsigned.pop("receipt_digest", None)
    if claimed_receipt != _digest(unsigned):
        raise ResearchFlowError("flow stage receipt digest mismatch")
    if artifacts.get("schema") != STAGE_ARTIFACT_SCHEMA:
        raise ResearchFlowError("staged input manifest schema mismatch")
    unsigned_artifacts = dict(artifacts)
    claimed_artifacts = unsigned_artifacts.pop("manifest_digest", None)
    if claimed_artifacts != _digest(unsigned_artifacts):
        raise ResearchFlowError("staged input manifest digest mismatch")
    rows = artifacts.get("files")
    if not isinstance(rows, list) or artifacts.get("files_digest") != _digest(rows):
        raise ResearchFlowError("staged input file digest mismatch")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ResearchFlowError("staged input entry is invalid")
        relative = _relative(row.get("path"), "staged input path")
        target = root / Path(*relative.parts)
        if (not target.is_file() or target.is_symlink()
                or target.stat().st_size != row.get("bytes")
                or _sha256_file(target) != row.get("sha256")):
            raise ResearchFlowError(f"staged input drifted: {relative}")
    inventory_root = Path(str(receipt.get("inventory_path") or "")).resolve()
    checked = verify_research_inventory(inventory_root)
    if checked["inventory_digest"] != receipt.get("inventory_digest"):
        raise ResearchFlowError("staged project inventory binding mismatch")
    _, manifest = _design(inventory_root, str(receipt.get("design_id") or ""))
    if manifest.get("manifest_digest") != receipt.get("design_manifest_digest"):
        raise ResearchFlowError("staged project design manifest drifted")
    for row in receipt.get("source_files") or []:
        relative = _relative(row.get("staged_path"), "staged source path")
        target = root / Path(*relative.parts)
        if _sha256_file(target) != row.get("sha256"):
            raise ResearchFlowError(f"staged RTL differs from frozen source: {relative}")
    return {
        "valid": True,
        "design_id": receipt["design_id"],
        "platform": receipt["platform"],
        "receipt_digest": claimed_receipt,
        "artifact_manifest_digest": claimed_artifacts,
        "inventory_digest": receipt["inventory_digest"],
        "source_unchanged": True,
        "logic_changes": receipt["logic_changes"],
        "stub_generated": receipt["stub_generated"],
        "flow_executed": receipt["flow_executed"],
    }


__all__ = [
    "ResearchFlowError", "STAGE_ARTIFACT_SCHEMA", "STAGE_RECEIPT_SCHEMA",
    "stage_flow_project", "verify_staged_flow_project",
]
