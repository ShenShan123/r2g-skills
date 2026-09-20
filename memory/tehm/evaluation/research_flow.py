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

from tehm.evaluation.research_epoch import verify_research_epoch
from tehm.evaluation.research_inventory import verify_research_inventory


STAGE_RECEIPT_SCHEMA = "tehm-research-flow-stage-v1"
STAGE_ARTIFACT_SCHEMA = "tehm-research-flow-stage-artifacts-v1"
FLOW_AUDIT_SCHEMA = "tehm-research-flow-audit-v1"
FLOW_AUDIT_ARTIFACT_SCHEMA = "tehm-research-flow-audit-artifacts-v1"
FLOW_STAGES = ("synth", "floorplan", "place", "cts", "route", "finish")


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


def _replace_sdc_line(text: str, pattern: str, replacement: str, label: str) -> str:
    compiled = re.compile(pattern, re.MULTILINE)
    matches = list(compiled.finditer(text))
    if len(matches) != 1:
        raise ResearchFlowError(
            f"constraint template must contain exactly one {label} declaration"
        )
    return compiled.sub(replacement, text)


def _retarget_sdc(
    text: str, *, top: str, binding: object,
) -> tuple[str, dict[str, Any] | None]:
    if binding in (None, {}):
        return text, None
    if not isinstance(binding, Mapping):
        raise ResearchFlowError("constraint binding must be an object")
    allowed = {
        "mode", "template_design", "template_clock_port", "target_clock_port",
        "clock_period_ns",
    }
    if set(binding) - allowed:
        raise ResearchFlowError("constraint binding contains unknown fields")
    if binding.get("mode") != "retarget_single_clock":
        raise ResearchFlowError("unsupported constraint binding mode")
    template_design = binding.get("template_design")
    template_clock = binding.get("template_clock_port")
    target_clock = binding.get("target_clock_port")
    period = binding.get("clock_period_ns")
    for value, label in (
        (template_design, "template design"),
        (template_clock, "template clock port"),
        (target_clock, "target clock port"),
    ):
        if type(value) is not str or not re.fullmatch(
            r"[A-Za-z_$][A-Za-z0-9_$]*", value
        ):
            raise ResearchFlowError(f"constraint {label} is invalid")
    if type(period) not in (int, float) or period <= 0:
        raise ResearchFlowError("constraint clock period must be positive")
    escaped_design = re.escape(str(template_design))
    escaped_clock = re.escape(str(template_clock))
    rewritten = _replace_sdc_line(
        text,
        rf"^[ \t]*current_design[ \t]+{escaped_design}[ \t]*$",
        f"current_design {top}",
        "template current_design",
    )
    rewritten = _replace_sdc_line(
        rewritten,
        rf"^[ \t]*set[ \t]+clk_port_name[ \t]+{escaped_clock}[ \t]*$",
        f"set clk_port_name {target_clock}",
        "template clk_port_name",
    )
    rewritten = _replace_sdc_line(
        rewritten,
        r"^[ \t]*set[ \t]+clk_period[ \t]+[^ \t\r\n]+[ \t]*$",
        f"set clk_period {period}",
        "template clk_period",
    )
    applied = {
        "mode": "retarget_single_clock",
        "template_design": template_design,
        "target_design": top,
        "template_clock_port": template_clock,
        "target_clock_port": target_clock,
        "clock_period_ns": period,
    }
    return rewritten, applied


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
        sdc_text, constraint_binding = _retarget_sdc(
            frozen_sdc.read_text(encoding="utf-8"),
            top=top,
            binding=flow.get("constraint_binding"),
        )
        staged_sdc.write_text(sdc_text, encoding="utf-8")
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
                "staged_sha256": _sha256_file(staged_sdc),
                "constraint_binding": constraint_binding,
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


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResearchFlowError(f"cannot read {label}: {path}") from exc
    if not lines:
        raise ResearchFlowError(f"{label} is empty")
    rows = []
    for number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResearchFlowError(f"invalid {label} line {number}") from exc
        if not isinstance(row, dict):
            raise ResearchFlowError(f"{label} row must be an object")
        rows.append(row)
    return rows


def _read_stage_log(path: Path) -> list[dict[str, Any]]:
    rows = _read_jsonl(path, "flow stage log")
    names = [row.get("stage") for row in rows]
    if names != list(FLOW_STAGES[:len(names)]):
        raise ResearchFlowError("flow stage log is not an ordered stage prefix")
    if len(names) > len(FLOW_STAGES):
        raise ResearchFlowError("flow stage log contains too many stages")
    for index, row in enumerate(rows):
        status = row.get("status")
        elapsed = row.get("elapsed_s")
        if type(status) is not int or type(elapsed) not in (int, float) or elapsed < 0:
            raise ResearchFlowError("flow stage status or elapsed time is invalid")
        if status != 0 and index != len(rows) - 1:
            raise ResearchFlowError("flow stage log continues after a failure")
    if all(row["status"] == 0 for row in rows) and len(rows) != len(FLOW_STAGES):
        raise ResearchFlowError("flow stage log stops without a terminal failure")
    return rows


def _verify_stage_artifacts(
    *,
    path: Path,
    run_root: Path,
    stages: list[dict[str, Any]],
    meta: Mapping[str, Any],
) -> list[Path]:
    rows = _read_jsonl(path, "stage artifact manifest")
    successful = [row for row in stages if row["status"] == 0]
    if len(rows) != len(successful):
        raise ResearchFlowError("stage artifact manifest coverage mismatch")
    preserved = []
    for stage, row in zip(successful, rows, strict=True):
        artifact = stage.get("artifact")
        expected = run_root / "final" / str(artifact or "")
        if (
            row.get("schema_version") != 1
            or row.get("stage_contract_version") != 2
            or row.get("stage") != stage["stage"]
            or row.get("status") != 0
            or row.get("run_tag") != meta.get("run_tag")
            or row.get("platform") != meta.get("platform")
            or row.get("design") != meta.get("design_name")
            or row.get("flow_variant") != meta.get("flow_variant")
            or row.get("toolchain") != meta.get("toolchain_fingerprint")
            or row.get("artifact") != artifact
        ):
            raise ResearchFlowError(
                f"stage artifact metadata mismatch: {stage['stage']}"
            )
        if not expected.is_file() or expected.is_symlink():
            raise ResearchFlowError(
                f"passing stage lacks preserved artifact: {stage['stage']}"
            )
        if (
            row.get("size") != expected.stat().st_size
            or row.get("sha256") != _sha256_file(expected).removeprefix("sha256:")
        ):
            raise ResearchFlowError(
                f"preserved stage artifact digest mismatch: {stage['stage']}"
            )
        preserved.append(expected)
    return preserved


def _auditor_binding(epoch_root: Path) -> dict[str, str]:
    manifest = _load_json(
        epoch_root / "bindings/oracle-binding.json", "auditor oracle binding"
    )
    source = Path(__file__).resolve()
    matches = [
        row for row in manifest.get("files") or []
        if isinstance(row, Mapping)
        and Path(str(row.get("source_path") or "")).resolve() == source
    ]
    if len(matches) != 1:
        raise ResearchFlowError(
            "auditor epoch must bind the executing research_flow.py exactly once"
        )
    row = matches[0]
    frozen_relative = _relative(row.get("frozen_path"), "frozen auditor path")
    frozen = epoch_root / Path(*frozen_relative.parts)
    current_sha = _sha256_file(source)
    if row.get("sha256") != current_sha or _sha256_file(frozen) != current_sha:
        raise ResearchFlowError("executing flow auditor differs from frozen auditor")
    return {
        "source_path": str(source),
        "sha256": current_sha,
        "frozen_path": str(row["frozen_path"]),
    }


def _verify_authority_fingerprint(
    fingerprint: str, authority: Mapping[str, Any]
) -> None:
    match = re.search(r"(?:^| )orfs=([^ ]+)@([0-9a-fA-F]{7,40})(?: |$)", fingerprint)
    if match is None:
        raise ResearchFlowError("flow run fingerprint lacks a parseable ORFS binding")
    expected_root = Path(str(authority.get("root") or "")).resolve()
    recorded_root = Path(match.group(1)).resolve()
    full_head = str(authority.get("git_head") or "").lower()
    short_head = match.group(2).lower()
    if recorded_root != expected_root or not full_head.startswith(short_head):
        raise ResearchFlowError("flow run fingerprint differs from adapter authority")


def _raw_reference(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ResearchFlowError(f"raw flow artifact is missing or unsafe: {path}")
    return {"path": str(path), "bytes": path.stat().st_size,
            "sha256": _sha256_file(path)}


def _flow_failure_class(status: int, log: str) -> tuple[str, str, str]:
    if status == 0:
        return "PASS", "flow_completed", "NONE"
    if "GRT-0232" in log and "Routing congestion too high" in log:
        return "FAIL", "routing_congestion", "FLOW_TARGET_FAILURE"
    if "PDN-0185" in log and "Insufficient width" in log:
        return "FAIL", "pdn_geometry_infeasible", "FLOW_TARGET_FAILURE"
    if status in (124, 137) or "TIMEOUT" in log.upper():
        return "UNKNOWN", "flow_timeout", "INFRASTRUCTURE_ERROR"
    infrastructure_markers = (
        "R2G_INPUTS_MISSING", "No space left on device", "Permission denied",
        "another run holds the ORFS workspace", "ORFS not found",
    )
    if any(marker in log for marker in infrastructure_markers):
        return "UNKNOWN", "infrastructure_failure", "INFRASTRUCTURE_ERROR"
    return "UNKNOWN", "unclassified_nonzero_flow_exit", "UNCLASSIFIED_FAILURE"


def _audit_manifest(root: Path) -> dict[str, Any]:
    self_path = root / "artifact-manifest.json"
    paths = [path for path in root.rglob("*") if path.is_file() and path != self_path]
    rows = [{
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    } for path in sorted(paths, key=lambda item: item.as_posix())]
    payload = {
        "schema": FLOW_AUDIT_ARTIFACT_SCHEMA,
        "files": rows,
        "files_digest": _digest(rows),
    }
    payload["manifest_digest"] = _digest(payload)
    return payload


def audit_flow_run(
    *,
    project: str | Path,
    run_dir: str | Path,
    producer_epoch: str | Path,
    auditor_epoch: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    """Independently classify one terminal fixed-flow attempt from raw outputs."""
    project_root = Path(project).expanduser().resolve()
    run_root = Path(run_dir).expanduser().resolve()
    producer_epoch_root = Path(producer_epoch).expanduser().resolve()
    auditor_epoch_root = Path(auditor_epoch).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchFlowError(f"refusing to overwrite flow audit: {destination}")
    try:
        run_root.relative_to(project_root / "backend")
    except ValueError as exc:
        raise ResearchFlowError(
            "flow run must be below the staged project backend"
        ) from exc
    staged = verify_staged_flow_project(project_root)
    checked_producer = verify_research_epoch(producer_epoch_root)
    checked_auditor = verify_research_epoch(auditor_epoch_root)
    if not checked_producer.get("valid") or not checked_producer.get(
        "research_evaluation_ready"
    ):
        raise ResearchFlowError("producer research epoch is not evaluation-ready")
    if not checked_auditor.get("valid") or not checked_auditor.get(
        "research_evaluation_ready"
    ):
        raise ResearchFlowError("auditor research epoch is not evaluation-ready")
    auditor_binding = _auditor_binding(auditor_epoch_root)
    receipt = _load_json(project_root / "stage-receipt.json", "flow stage receipt")
    meta_path = run_root / "run-meta.json"
    stage_path = run_root / "stage_log.jsonl"
    stage_artifact_path = run_root / "stage_artifact_manifest.jsonl"
    flow_log_path = run_root / "flow.log"
    meta = _load_json(meta_path, "flow run metadata")
    stages = _read_stage_log(stage_path)
    try:
        flow_log = flow_log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ResearchFlowError("cannot read raw flow log") from exc
    if meta.get("design_name") != receipt.get("top_module"):
        raise ResearchFlowError("flow run design does not match staged top")
    if meta.get("design_nickname") != receipt.get("design_id"):
        raise ResearchFlowError("flow run nickname does not match staged design")
    if meta.get("platform") != receipt.get("platform"):
        raise ResearchFlowError("flow run platform does not match staged platform")
    if Path(str(meta.get("config_mk") or "")).resolve() != (
        project_root / "constraints/config.mk"
    ):
        raise ResearchFlowError("flow run config path is not staged input")
    if Path(str(meta.get("sdc_file") or "")).resolve() != (
        project_root / "constraints/constraint.sdc"
    ):
        raise ResearchFlowError("flow run SDC path is not staged input")
    toolchain = _load_json(
        producer_epoch_root / "bindings/toolchain-manifest.json",
        "frozen producer toolchain manifest",
    )
    tools = toolchain.get("tools") or {}
    if meta.get("openroad_exe") != (tools.get("openroad") or {}).get("path"):
        raise ResearchFlowError("flow run OpenROAD path differs from frozen toolchain")
    if meta.get("yosys_exe") != (tools.get("yosys") or {}).get("path"):
        raise ResearchFlowError("flow run Yosys path differs from frozen toolchain")
    authority = receipt.get("authority_checkout") or {}
    fingerprint = str(meta.get("toolchain_fingerprint") or "")
    _verify_authority_fingerprint(fingerprint, authority)
    make_status = meta.get("make_status")
    if type(make_status) is not int or make_status != stages[-1]["status"]:
        raise ResearchFlowError("flow run status disagrees with terminal stage")
    preserved_artifacts = _verify_stage_artifacts(
        path=stage_artifact_path, run_root=run_root, stages=stages, meta=meta
    )
    verdict, reason, failure_layer = _flow_failure_class(make_status, flow_log)
    checks = {
        "source_integrity": "PASS" if staged["source_unchanged"] else "FAIL",
        "toolchain_binding": "PASS",
    }
    for name in FLOW_STAGES:
        match = next((row for row in stages if row["stage"] == name), None)
        if match is None:
            checks[name] = "NOT_EXECUTED"
        elif match["status"] == 0:
            checks[name] = "PASS"
        elif name == stages[-1]["stage"]:
            checks[name] = verdict
        else:
            checks[name] = "UNKNOWN"
    flow_completion = (
        "PASS" if all(checks[name] == "PASS" for name in FLOW_STAGES)
        else "FAIL" if verdict == "FAIL" else "UNKNOWN"
    )
    checks["flow_completion"] = flow_completion
    raw_refs = [_raw_reference(path) for path in (
        meta_path, stage_path, stage_artifact_path, flow_log_path,
    )]
    raw_refs.extend(_raw_reference(path) for path in preserved_artifacts)
    audit = {
        "schema": FLOW_AUDIT_SCHEMA,
        "design_id": receipt["design_id"],
        "run_tag": meta.get("run_tag"),
        "flow_variant": meta.get("flow_variant"),
        "stage_receipt_digest": staged["receipt_digest"],
        "inventory_digest": staged["inventory_digest"],
        "project_path": str(project_root),
        "run_dir": str(run_root),
        "producer_epoch_path": str(producer_epoch_root),
        "producer_epoch_id": checked_producer["epoch_id"],
        "producer_epoch_digest": checked_producer["epoch_digest"],
        "producer_epoch_binding": "caller_supplied_and_toolchain_consistent",
        "auditor_epoch_path": str(auditor_epoch_root),
        "auditor_epoch_id": checked_auditor["epoch_id"],
        "auditor_epoch_digest": checked_auditor["epoch_digest"],
        "auditor_binding": auditor_binding,
        "toolchain_manifest_digest": checked_producer["toolchain_manifest_digest"],
        "scope": {
            "required_flow_stages": list(FLOW_STAGES),
            "strict_signoff_checks": "out_of_scope",
            "functional_repair": "out_of_scope",
        },
        "constraint_binding": (receipt.get("sdc_template") or {}).get(
            "constraint_binding"
        ),
        "checks": checks,
        "terminal_stage": stages[-1]["stage"],
        "terminal_exit_code": make_status,
        "oracle_verdict": flow_completion,
        "oracle_reason": reason,
        "failure_layer": failure_layer,
        "execution_status": "COMPLETED" if make_status == 0 else "FAILED",
        "actual_cost": {
            "flow_driver_calls": 1,
            "eda_stage_calls": len(stages),
            "model_calls": 0,
            "model_tokens": 0,
            "stage_wallclock_seconds": sum(float(row["elapsed_s"]) for row in stages),
            "independent_audit_calls": 1,
        },
        "raw_artifacts": raw_refs,
        "source_mutation": "none" if staged["source_unchanged"] else "detected",
        "memory_update": "none",
        "production_authority": False,
        "claim_boundary": (
            "Fixed-flow onboarding only; a PASS would establish scoped flow completion, "
            "not functional repair, memory efficacy, broad signoff, or production readiness."
        ),
    }
    audit["audit_digest"] = _digest(audit)
    summary = {
        "schema": "tehm-research-flow-audit-summary-v1",
        "design_id": audit["design_id"],
        "audit_digest": audit["audit_digest"],
        "oracle_verdict": audit["oracle_verdict"],
        "oracle_reason": audit["oracle_reason"],
        "failure_layer": audit["failure_layer"],
        "terminal_stage": audit["terminal_stage"],
        "actual_cost": audit["actual_cost"],
        "claim_boundary": audit["claim_boundary"],
    }
    summary["summary_digest"] = _digest(summary)
    staging_output = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    if staging_output.exists():
        raise ResearchFlowError(f"flow audit staging exists: {staging_output}")
    staging_output.mkdir(parents=True)
    try:
        _write_json(staging_output / "flow-audit.json", audit)
        _write_json(staging_output / "summary.json", summary)
        _write_json(
            staging_output / "artifact-manifest.json",
            _audit_manifest(staging_output),
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging_output.replace(destination)
    except Exception:
        shutil.rmtree(staging_output, ignore_errors=True)
        raise
    return verify_flow_audit(destination)


def verify_flow_audit(path: str | Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    audit = _load_json(root / "flow-audit.json", "flow audit")
    summary = _load_json(root / "summary.json", "flow audit summary")
    artifacts = _load_json(root / "artifact-manifest.json", "flow audit artifacts")
    if audit.get("schema") != FLOW_AUDIT_SCHEMA:
        raise ResearchFlowError("flow audit schema mismatch")
    unsigned_audit = dict(audit)
    audit_digest = unsigned_audit.pop("audit_digest", None)
    if audit_digest != _digest(unsigned_audit):
        raise ResearchFlowError("flow audit digest mismatch")
    unsigned_summary = dict(summary)
    summary_digest = unsigned_summary.pop("summary_digest", None)
    if (
        summary_digest != _digest(unsigned_summary)
        or summary.get("audit_digest") != audit_digest
    ):
        raise ResearchFlowError("flow audit summary binding mismatch")
    if artifacts.get("schema") != FLOW_AUDIT_ARTIFACT_SCHEMA:
        raise ResearchFlowError("flow audit artifact schema mismatch")
    unsigned_artifacts = dict(artifacts)
    artifact_digest = unsigned_artifacts.pop("manifest_digest", None)
    if artifact_digest != _digest(unsigned_artifacts):
        raise ResearchFlowError("flow audit artifact digest mismatch")
    rows = artifacts.get("files")
    if not isinstance(rows, list) or artifacts.get("files_digest") != _digest(rows):
        raise ResearchFlowError("flow audit artifact file digest mismatch")
    for row in rows:
        relative = _relative(row.get("path"), "flow audit artifact path")
        target = root / Path(*relative.parts)
        if (not target.is_file() or target.stat().st_size != row.get("bytes")
                or _sha256_file(target) != row.get("sha256")):
            raise ResearchFlowError(f"flow audit artifact drifted: {relative}")
    for row in audit.get("raw_artifacts") or []:
        target = Path(str(row.get("path") or ""))
        if (not target.is_file() or target.is_symlink()
                or target.stat().st_size != row.get("bytes")
                or _sha256_file(target) != row.get("sha256")):
            raise ResearchFlowError(f"raw flow artifact drifted: {target}")
    producer = verify_research_epoch(audit.get("producer_epoch_path"))
    auditor = verify_research_epoch(audit.get("auditor_epoch_path"))
    if (
        producer.get("epoch_id") != audit.get("producer_epoch_id")
        or producer.get("epoch_digest") != audit.get("producer_epoch_digest")
        or auditor.get("epoch_id") != audit.get("auditor_epoch_id")
        or auditor.get("epoch_digest") != audit.get("auditor_epoch_digest")
    ):
        raise ResearchFlowError("flow audit epoch binding drifted")
    staged = verify_staged_flow_project(audit.get("project_path"))
    if staged.get("receipt_digest") != audit.get("stage_receipt_digest"):
        raise ResearchFlowError("flow audit staged-project binding drifted")
    return {
        "valid": True,
        "design_id": audit["design_id"],
        "audit_digest": audit_digest,
        "summary_digest": summary_digest,
        "artifact_manifest_digest": artifact_digest,
        "oracle_verdict": audit["oracle_verdict"],
        "oracle_reason": audit["oracle_reason"],
        "failure_layer": audit["failure_layer"],
        "terminal_stage": audit["terminal_stage"],
        "actual_cost": audit["actual_cost"],
    }


__all__ = [
    "FLOW_AUDIT_ARTIFACT_SCHEMA", "FLOW_AUDIT_SCHEMA", "ResearchFlowError",
    "STAGE_ARTIFACT_SCHEMA", "STAGE_RECEIPT_SCHEMA", "audit_flow_run",
    "stage_flow_project", "verify_flow_audit", "verify_staged_flow_project",
]
