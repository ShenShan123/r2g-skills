"""Read-only real-design inventory for TEHM Research Runtime RC1.

The inventory is deliberately conservative.  It records explicit project
metadata when available, reuses the rtl-acquire dependency parser, and labels
all inferred identity/top/clock information as such.  It never writes below the
corpus root, never follows symlinks, never fabricates missing RTL, and does not
promote a directory scan to flow or repair readiness.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any


INVENTORY_SCHEMA = "tehm-research-design-inventory-v1"
DESIGN_MANIFEST_SCHEMA = "tehm-research-design-manifest-v1"
EXCLUSION_SCHEMA = "tehm-research-design-exclusions-v1"
ARTIFACT_MANIFEST_SCHEMA = "tehm-research-inventory-artifacts-v1"
PREFLIGHT_SHORTLIST_SCHEMA = "tehm-research-preflight-shortlist-v1"
ADAPTER_SPEC_SCHEMA = "tehm-research-inventory-adapter-spec-v1"
ADAPTER_SPEC_V2_SCHEMA = "tehm-research-inventory-adapter-spec-v2"

HDL_SUFFIXES = {".v", ".sv", ".vhd", ".vhdl"}
VERILOG_SUFFIXES = {".v", ".sv"}
HEADER_SUFFIXES = {".vh", ".svh", ".h", ".inc"}
CONSTRAINT_SUFFIXES = {".sdc", ".xdc"}
INIT_SUFFIXES = {".mem", ".hex", ".mif", ".coe", ".bin"}
MACRO_VIEW_SUFFIXES = {".lib", ".lef", ".gds", ".gds2", ".db"}
METADATA_NAMES = {
    "config.tcl", "config.mk", "design_config.mk", "design_meta.json",
    "src_manifest.txt", "sources.f", "filelist.f", ".gitmodules",
    "Makefile", "CMakeLists.txt", "fusesoc.conf",
}
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", ".orfs-work"}
PROJECT_MARKERS = {
    "config.tcl", "config.mk", "design_config.mk", "design_meta.json",
    "src_manifest.txt", "sources.f", "filelist.f", ".git", ".gitmodules",
}
TB_MARKERS = {"tb", "test", "tests", "testbench", "bench", "sim", "simulation"}
FORMAL_MARKERS = {"formal", "sva", "property", "properties"}
GENERATED_MARKERS = {"generated", "genrtl", "gen", "build", "out", "obj_dir"}


class ResearchInventoryError(ValueError):
    """Raised when a read-only inventory cannot be built or verified."""


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
        raise ResearchInventoryError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchInventoryError(f"{label} must be a JSON object")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ResearchInventoryError(f"path escapes corpus root: {path}") from exc


def _walk(root: Path) -> tuple[list[Path], list[dict[str, Any]], list[str]]:
    """Return regular files, symlink records, and read errors without following links."""
    files: list[Path] = []
    links: list[dict[str, Any]] = []
    errors: list[str] = []

    def onerror(error: OSError) -> None:
        filename = str(getattr(error, "filename", "") or "")
        errors.append(f"walk_error:{filename}:{type(error).__name__}")

    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False,
                                                 onerror=onerror):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for name in sorted(dirnames):
            candidate = current_path / name
            rel = candidate.relative_to(root)
            if name in SKIP_PARTS:
                continue
            if candidate.is_symlink():
                target = os.readlink(candidate)
                resolved = candidate.resolve(strict=False)
                links.append({
                    "path": rel.as_posix(),
                    "kind": "directory_symlink",
                    "target": target,
                    "escapes_corpus": not _inside(resolved, root),
                })
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in sorted(filenames):
            candidate = current_path / name
            rel = candidate.relative_to(root)
            if candidate.is_symlink():
                target = os.readlink(candidate)
                resolved = candidate.resolve(strict=False)
                links.append({
                    "path": rel.as_posix(),
                    "kind": "file_symlink",
                    "target": target,
                    "escapes_corpus": not _inside(resolved, root),
                })
                continue
            try:
                if candidate.is_file():
                    files.append(candidate)
            except OSError as exc:
                errors.append(f"stat_error:{rel.as_posix()}:{type(exc).__name__}")
    return sorted(files), sorted(links, key=lambda row: row["path"]), sorted(errors)


def _snapshot(root: Path) -> dict[str, Any]:
    files, links, errors = _walk(root)
    entries: list[dict[str, Any]] = []
    for path in files:
        try:
            stat = path.stat()
        except OSError as exc:
            errors.append(
                f"stat_error:{path.relative_to(root).as_posix()}:{type(exc).__name__}"
            )
            continue
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "kind": "file",
            "bytes": stat.st_size,
            "mode": stat.st_mode & 0o777,
            "mtime_ns": stat.st_mtime_ns,
        })
    entries.extend({
        "path": row["path"],
        "kind": row["kind"],
        "target": row["target"],
        "escapes_corpus": row["escapes_corpus"],
    } for row in links)
    entries.sort(key=lambda row: (row["path"], row["kind"]))
    return {
        "entry_count": len(entries),
        "error_count": len(errors),
        "errors": sorted(set(errors)),
        "entries_digest": _digest(entries),
    }


def _project_roots(corpus: Path, files: Sequence[Path]) -> tuple[list[Path], list[dict[str, Any]]]:
    by_first: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        relative = path.relative_to(corpus)
        if relative.parts:
            by_first[relative.parts[0]].append(path)

    projects: list[Path] = []
    exclusions: list[dict[str, Any]] = []
    for first_name in sorted(by_first):
        first = corpus / first_name
        if not first.is_dir():
            continue
        first_files = by_first[first_name]
        direct_names = {
            path.name for path in first_files if len(path.relative_to(first).parts) == 1
        }
        direct_hdl = any(
            path.suffix.lower() in HDL_SUFFIXES
            for path in first_files if len(path.relative_to(first).parts) == 1
        )
        child_hdl: dict[str, list[Path]] = defaultdict(list)
        for path in first_files:
            relative = path.relative_to(first)
            if len(relative.parts) >= 2 and path.suffix.lower() in HDL_SUFFIXES:
                child_hdl[relative.parts[0]].append(path)

        marked_children = {
            child_name for child_name in child_hdl
            if any(
                path.name in PROJECT_MARKERS
                and path.is_relative_to(first / child_name)
                and len(path.relative_to(first / child_name).parts) == 1
                for path in first_files
            )
        }
        if direct_hdl or direct_names & PROJECT_MARKERS:
            if any(path.suffix.lower() in HDL_SUFFIXES for path in first_files):
                projects.append(first)
            else:
                exclusions.append({
                    "path": first_name,
                    "reason": "no_hdl_source",
                    "scope": "catalog_entry",
                })
            continue

        if not marked_children and len(child_hdl) <= 1:
            projects.append(first)
            continue

        for child_name, hdl_files in sorted(child_hdl.items()):
            child = first / child_name
            child_direct = {
                path.name for path in first_files
                if path.is_relative_to(child)
                and len(path.relative_to(child).parts) == 1
            }
            if hdl_files or child_direct & PROJECT_MARKERS:
                projects.append(child)
        unassigned = [
            path for path in first_files
            if path.suffix.lower() in HDL_SUFFIXES
            and not any(path.is_relative_to(project) for project in projects)
        ]
        if unassigned:
            exclusions.append({
                "path": first_name,
                "reason": "ambiguous_project_boundary",
                "scope": "catalog_entry",
                "unassigned_hdl_count": len(unassigned),
            })
    return sorted(set(projects)), sorted(exclusions, key=lambda row: row["path"])


def _load_acquisition_parser(script: Path):
    if not script.is_file():
        raise ResearchInventoryError(f"rtl-acquire parser is missing: {script}")
    scripts_root = script.parents[1]
    inserted = False
    if str(scripts_root) not in sys.path:
        sys.path.insert(0, str(scripts_root))
        inserted = True
    try:
        spec = importlib.util.spec_from_file_location("tehm_rtl_acquire_discovery", script)
        if spec is None or spec.loader is None:
            raise ResearchInventoryError("cannot load rtl-acquire parser")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ResearchInventoryError("cannot initialize rtl-acquire parser") from exc
    finally:
        if inserted:
            sys.path.remove(str(scripts_root))
    required = (
        "extract_module_defs", "extract_instantiated_modules", "collect_defines",
        "extract_macro_instantiations", "resolve_macro_refs",
    )
    if any(not callable(getattr(module, name, None)) for name in required):
        raise ResearchInventoryError("rtl-acquire parser contract is incomplete")
    return module


def _read_text(path: Path) -> tuple[str, str | None]:
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return "", f"read_error:{path.name}:{type(exc).__name__}"


def _config_values(project: Path, files: Sequence[Path]) -> dict[str, Any]:
    explicit_tops: list[dict[str, str]] = []
    clocks: list[dict[str, Any]] = []
    source_urls: list[dict[str, str]] = []
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    meta_path = project / "design_meta.json"
    if meta_path in files:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"invalid_design_meta:{type(exc).__name__}")
        else:
            if isinstance(meta, dict):
                metadata = meta
                top = meta.get("top")
                if isinstance(top, str) and top.strip():
                    explicit_tops.append({"value": top.strip(), "source": "design_meta.json"})
                for key in ("repository_url", "repo_url", "source_url"):
                    value = meta.get(key)
                    if isinstance(value, str) and value.strip():
                        source_urls.append({"value": value.strip(), "source": f"design_meta.json:{key}"})

    for path in files:
        if path.name not in {"config.tcl", "config.mk", "design_config.mk"}:
            continue
        text, error = _read_text(path)
        if error:
            warnings.append(error)
            continue
        rel = path.relative_to(project).as_posix()
        patterns = (
            r"(?m)^\s*set\s+(?:TOP_NAME|DESIGN_NAME)\s+[\"']?([^\"'\s]+)",
            r"(?m)^\s*(?:export\s+)?DESIGN_NAME\s*[:?+]?=\s*([^\s#]+)",
        )
        for pattern in patterns:
            for value in re.findall(pattern, text):
                explicit_tops.append({"value": value.strip(), "source": rel})
        clock_names = []
        for pattern in (
            r"(?m)^\s*set\s+(?:CLOCK_NAME|CLOCK_PORT)\s+[\"']?([^\"'\s]+)",
            r"(?m)^\s*(?:export\s+)?CLOCK_PORT\s*[:?+]?=\s*([^\s#]+)",
        ):
            clock_names.extend(value.strip() for value in re.findall(pattern, text))
        periods = []
        for pattern in (
            r"(?m)^\s*set\s+(?:clk_period|CLOCK_PERIOD)\s+[\"']?([0-9.]+)",
            r"(?m)^\s*(?:export\s+)?CLOCK_PERIOD\s*[:?+]?=\s*([0-9.]+)",
        ):
            periods.extend(value.strip() for value in re.findall(pattern, text))
        for index, name in enumerate(clock_names):
            period: float | None = None
            if periods:
                try:
                    period = float(periods[min(index, len(periods) - 1)])
                except ValueError:
                    warnings.append(f"invalid_clock_period:{rel}")
            clocks.append({"name": name, "period_ns": period, "source": rel,
                           "authority": "explicit_config"})

    def unique(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        output = []
        for row in rows:
            key = (str(row.get("value") or row.get("name")), str(row.get("source")))
            if key not in seen:
                output.append(dict(row))
                seen.add(key)
        return output

    return {
        "explicit_tops": unique(explicit_tops),
        "clocks": unique(clocks),
        "source_urls": unique(source_urls),
        "design_meta": metadata,
        "warnings": sorted(set(warnings)),
    }


def _git_identity(project: Path) -> dict[str, Any] | None:
    if not (project / ".git").exists():
        return None

    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(project), *args], check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    return {
        "revision": run("rev-parse", "HEAD"),
        "remote_origin": run("remote", "get-url", "origin"),
        "dirty": bool(run("status", "--porcelain=v1")),
    }


def _ordered_filelist(project: Path, files: Sequence[Path], hdl: Sequence[Path],
                      headers: Sequence[Path]) -> tuple[list[Path], str, list[str]]:
    warnings: list[str] = []
    candidates = [project / name for name in ("src_manifest.txt", "sources.f", "filelist.f")]
    for manifest in candidates:
        if manifest not in files:
            continue
        ordered: list[Path] = []
        text, error = _read_text(manifest)
        if error:
            warnings.append(error)
            continue
        for line_number, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            line = line.split("#", 1)[0].strip()
            source = Path(line)
            if source.is_absolute() or ".." in source.parts:
                warnings.append(f"unsafe_filelist_entry:{manifest.name}:{line_number}")
                continue
            resolved = (project / source).resolve(strict=False)
            if not _inside(resolved, project) or not resolved.is_file():
                warnings.append(f"missing_filelist_entry:{manifest.name}:{line_number}:{line}")
                continue
            if resolved.suffix.lower() in HDL_SUFFIXES | HEADER_SUFFIXES:
                ordered.append(resolved)
        if ordered:
            remainder = [path for path in [*hdl, *headers] if path not in ordered]
            return [*ordered, *sorted(remainder)], f"explicit:{manifest.name}", warnings
    return sorted([*hdl, *headers]), "inferred:lexicographic", warnings


def _project_links(project: Path, links: Sequence[Mapping[str, Any]], corpus: Path) -> list[dict[str, Any]]:
    prefix = project.relative_to(corpus)
    output = []
    for row in links:
        relative = PurePosixPath(str(row["path"]))
        if tuple(relative.parts[:len(prefix.parts)]) != prefix.parts:
            continue
        local = PurePosixPath(*relative.parts[len(prefix.parts):]).as_posix()
        item = dict(row)
        item["path"] = local
        output.append(item)
    return output


def _source_entry(path: Path, project: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": path.relative_to(project).as_posix(),
        "bytes": stat.st_size,
        "sha256": _sha256_file(path),
    }


def _identifier(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-.").lower()
    return cleaned or "design"


def _origin(project: Path, config: Mapping[str, Any], git: Mapping[str, Any] | None,
            corpus: Path) -> dict[str, Any]:
    urls = list(config.get("source_urls") or [])
    if git and git.get("remote_origin"):
        urls.append({"value": git["remote_origin"], "source": "git:origin"})
    metadata = config.get("design_meta") or {}
    declared_repo = None
    notes = metadata.get("notes") if isinstance(metadata, Mapping) else None
    if isinstance(notes, str):
        match = re.search(r"(?:^|[;\s])repo=([^;\s]+)", notes)
        if match:
            declared_repo = match.group(1)
    if urls:
        identity = str(urls[0]["value"])
        lineage = "origin:" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        status = "explicit_origin_unverified"
    elif declared_repo:
        lineage = f"declared-repo:{declared_repo}"
        status = "declared_metadata_unverified"
    else:
        lineage = None
        status = "unresolved"
    relative = project.relative_to(corpus)
    return {
        "source_urls": urls,
        "declared_repository": declared_repo,
        "lineage_group": lineage,
        "lineage_status": status,
        "catalog_bucket": relative.parts[0] if relative.parts else None,
        "catalog_bucket_is_not_lineage": True,
    }


def _manifest(project: Path, corpus: Path, project_files: Sequence[Path],
              links: Sequence[Mapping[str, Any]], parser: Any,
              parser_binding: Mapping[str, Any]) -> dict[str, Any]:
    relative_project = project.relative_to(corpus).as_posix()
    hdl = sorted(path for path in project_files if path.suffix.lower() in HDL_SUFFIXES)
    headers = sorted(path for path in project_files if path.suffix.lower() in HEADER_SUFFIXES)
    constraints = sorted(path for path in project_files if path.suffix.lower() in CONSTRAINT_SUFFIXES)
    init_files = sorted(path for path in project_files if path.suffix.lower() in INIT_SUFFIXES)
    macro_views = sorted(path for path in project_files if path.suffix.lower() in MACRO_VIEW_SUFFIXES)
    metadata_files = sorted(
        path for path in project_files
        if path.name in METADATA_NAMES
        or path.name.lower().startswith(("readme", "license", "copying", "notice"))
        or path.suffix.lower() == ".core"
    )
    testbenches = sorted(
        path for path in project_files
        if path.suffix.lower() in HDL_SUFFIXES
        and (set(part.lower() for part in path.relative_to(project).parts[:-1]) & TB_MARKERS
             or re.search(r"(^|[_-])(tb|test)([_-]|$)", path.stem.lower()))
    )
    formal = sorted(
        path for path in project_files
        if set(part.lower() for part in path.relative_to(project).parts[:-1]) & FORMAL_MARKERS
        or path.suffix.lower() in {".sby", ".sva"}
    )
    generated = sorted(
        path for path in hdl
        if set(part.lower() for part in path.relative_to(project).parts[:-1]) & GENERATED_MARKERS
    )
    input_files = sorted(set([*hdl, *headers, *constraints, *init_files,
                              *macro_views, *metadata_files, *formal]))
    source_entries = [_source_entry(path, project) for path in input_files]
    source_bundle_digest = _digest(source_entries)
    config = _config_values(project, project_files)
    git = _git_identity(project)
    origin = _origin(project, config, git, corpus)
    ordered, filelist_authority, filelist_warnings = _ordered_filelist(
        project, project_files, hdl, headers
    )

    module_to_path: dict[str, Path] = {}
    definitions: dict[str, str] = {}
    instantiated: Counter[str] = Counter()
    parser_warnings: list[str] = []
    verilog = [path for path in hdl if path.suffix.lower() in VERILOG_SUFFIXES]
    defines = parser.collect_defines([*verilog, *headers])
    per_file: dict[Path, dict[str, Any]] = {}
    for path in verilog:
        text, error = _read_text(path)
        if error:
            parser_warnings.append(error)
            continue
        module_defs = list(parser.extract_module_defs(text))
        refs = set(parser.extract_instantiated_modules(text))
        macros = set(parser.extract_macro_instantiations(text))
        per_file[path] = {"defs": module_defs, "refs": refs, "macros": macros}
        for name in module_defs:
            if name in module_to_path:
                parser_warnings.append(f"duplicate_module_definition:{name}")
                continue
            module_to_path[name] = path
            definitions[name] = path.relative_to(project).as_posix()
    unresolved_macros: set[str] = set()
    local_edges: list[dict[str, str]] = []
    for path, info in per_file.items():
        macro_refs, missing_macros = parser.resolve_macro_refs(
            info["macros"], defines, module_to_path
        )
        unresolved_macros.update(missing_macros)
        for ref in set(info["refs"]) | set(macro_refs):
            instantiated[ref] += 1
            if ref in module_to_path and module_to_path[ref] != path:
                local_edges.append({
                    "from_file": path.relative_to(project).as_posix(),
                    "module": ref,
                    "to_file": module_to_path[ref].relative_to(project).as_posix(),
                })
    top_candidates = sorted(name for name in definitions if not instantiated[name])
    explicit_tops = list(config["explicit_tops"])
    explicit_values = sorted(set(row["value"] for row in explicit_tops))
    if len(explicit_values) == 1:
        top = explicit_values[0]
        top_authority = "explicit_config"
    elif not explicit_values and len(top_candidates) == 1:
        top = top_candidates[0]
        top_authority = "heuristic_unverified"
    else:
        top = None
        top_authority = "unresolved"
    if top and top not in definitions and verilog:
        parser_warnings.append(f"declared_top_not_defined:{top}")
    if len(explicit_values) > 1:
        parser_warnings.append("conflicting_explicit_top")

    include_re = re.compile(r"`include\s+[\"<]([^\">]+)[\">]")
    missing_includes: set[str] = set()
    resolved_includes: set[str] = set()
    by_name: dict[str, list[Path]] = defaultdict(list)
    for path in [*headers, *verilog]:
        by_name[path.name].append(path)
    for path in verilog:
        text, _ = _read_text(path)
        for include in include_re.findall(text):
            direct = (path.parent / include).resolve(strict=False)
            if _inside(direct, project) and direct.is_file():
                resolved_includes.add(direct.relative_to(project).as_posix())
            elif len(by_name[Path(include).name]) == 1:
                resolved_includes.add(by_name[Path(include).name][0].relative_to(project).as_posix())
            else:
                missing_includes.add(include)
    include_dirs = sorted({
        str(PurePosixPath(item).parent) for item in resolved_includes
    } | {path.parent.relative_to(project).as_posix() for path in headers})

    project_links = _project_links(project, links, corpus)
    blockers: list[str] = [*config["warnings"], *filelist_warnings, *parser_warnings]
    if not hdl:
        blockers.append("no_hdl_source")
    if all(path.suffix.lower() in {".vhd", ".vhdl"} for path in hdl):
        blockers.append("vhdl_only_requires_adapter")
    if missing_includes:
        blockers.append("missing_include_dependency")
    if any(row["escapes_corpus"] for row in project_links):
        blockers.append("symlink_escapes_corpus")
    if top is None:
        blockers.append("top_unresolved")
    if filelist_authority.startswith("inferred"):
        blockers.append("ordered_filelist_unverified")
    blockers.append("inventory_only_no_frontend_preflight")
    readiness = (
        "UNSUPPORTED_CURRENT_PROFILE"
        if any(item in blockers for item in (
            "no_hdl_source", "vhdl_only_requires_adapter", "symlink_escapes_corpus"
        )) else "NEEDS_ADAPTER"
    )

    design_id = _identifier(relative_project.replace("/", "--"))
    declared_design = (config.get("design_meta") or {}).get("design")
    if isinstance(declared_design, str) and declared_design.strip():
        design_id = _identifier(declared_design)
    prior_status = (config.get("design_meta") or {}).get("status")
    manifest = {
        "schema": DESIGN_MANIFEST_SCHEMA,
        "design_id": design_id,
        "project_path": relative_project,
        "source_root": str(corpus),
        "identity": {
            "origin": origin,
            "git": git,
            "license_files": [path.relative_to(project).as_posix() for path in metadata_files
                              if path.name.lower().startswith(("license", "copying", "notice"))],
            "access_policy": "unknown",
            "source_bundle_digest": source_bundle_digest,
            "fork_or_derived_relation": {
                "status": "unresolved",
                "peer_design_ids": [],
            },
        },
        "compilation": {
            "top_module": top,
            "top_authority": top_authority,
            "top_candidates": top_candidates,
            "top_evidence": explicit_tops,
            "ordered_filelist": [path.relative_to(project).as_posix() for path in ordered],
            "filelist_authority": filelist_authority,
            "language": sorted(set(path.suffix.lower().lstrip(".") for path in hdl)),
            "frontend": "unverified",
            "include_dirs": include_dirs,
            "defines": dict(sorted(defines.items())),
            "top_parameters": {},
        },
        "dependencies": {
            "module_definitions": dict(sorted(definitions.items())),
            "local_module_edges": sorted(local_edges, key=lambda row: (
                row["from_file"], row["module"], row["to_file"])),
            "unresolved_instantiations": sorted(name for name in instantiated if name not in definitions),
            "unresolved_macros": sorted(unresolved_macros),
            "resolved_includes": sorted(resolved_includes),
            "missing_includes": sorted(missing_includes),
            "submodule_manifest": ".gitmodules" if (project / ".gitmodules").is_file() else None,
            "memory_init_files": [path.relative_to(project).as_posix() for path in init_files],
            "generated_rtl": [path.relative_to(project).as_posix() for path in generated],
            "macro_views": [path.relative_to(project).as_posix() for path in macro_views],
            "symlinks_not_followed": project_links,
        },
        "clock_and_constraints": {
            "clocks": config["clocks"],
            "clock_domain_count": len(config["clocks"]) if config["clocks"] else None,
            "reset": "unknown",
            "sdc_files": [path.relative_to(project).as_posix() for path in constraints
                          if path.suffix.lower() == ".sdc"],
            "io_constraints": "unknown",
            "exceptions": "unknown",
        },
        "verification": {
            "testbench_files": [path.relative_to(project).as_posix() for path in testbenches],
            "target_test": None,
            "regression_test": None,
            "formal_files": [path.relative_to(project).as_posix() for path in formal],
            "functional_repair_oracle": "unverified" if testbenches else "unavailable",
            "equivalence_scope": "unverified",
        },
        "size": {
            "source_file_count": len(hdl),
            "source_bytes": sum(path.stat().st_size for path in hdl),
            "synth_cell_count": None,
            "memory_bits": None,
            "area": None,
        },
        "readiness": {
            "status": readiness,
            "reasons": sorted(set(blockers)),
            "ready_rtl_repair": False,
            "ready_flow": False,
            "prior_flow_status": prior_status if isinstance(prior_status, str) else None,
            "prior_flow_status_authority": "historical_metadata_unverified" if prior_status else None,
            "stub_generated": False,
        },
        "parser_binding": dict(parser_binding),
        "source_files": source_entries,
        "source_bundle_digest": source_bundle_digest,
    }
    manifest["manifest_digest"] = _digest(manifest)
    return manifest


def _artifact_manifest(root: Path) -> dict[str, Any]:
    path_self = root / "artifact-manifest.json"
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file() and path != path_self:
            files.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            })
    payload = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "files": files,
        "files_digest": _digest(files),
    }
    payload["manifest_digest"] = _digest(payload)
    return payload


def _write_csv(path: Path, manifests: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "design_id", "project_path", "lineage_group", "lineage_status",
        "top_module", "top_authority", "source_file_count", "source_bundle_digest",
        "readiness", "functional_repair_oracle", "prior_flow_status",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for manifest in manifests:
            identity = manifest["identity"]
            origin = identity["origin"]
            compilation = manifest["compilation"]
            readiness = manifest["readiness"]
            writer.writerow({
                "design_id": manifest["design_id"],
                "project_path": manifest["project_path"],
                "lineage_group": origin["lineage_group"] or "",
                "lineage_status": origin["lineage_status"],
                "top_module": compilation["top_module"] or "",
                "top_authority": compilation["top_authority"],
                "source_file_count": manifest["size"]["source_file_count"],
                "source_bundle_digest": manifest["source_bundle_digest"],
                "readiness": readiness["status"],
                "functional_repair_oracle": manifest["verification"]["functional_repair_oracle"],
                "prior_flow_status": readiness["prior_flow_status"] or "",
            })


def _preflight_shortlist(manifests: Sequence[Mapping[str, Any]], *, limit: int = 8) -> dict[str, Any]:
    """Select bounded parser-clean proposals without upgrading their readiness."""
    disallowed_reason_prefixes = (
        "conflicting_explicit_top", "declared_top_not_defined",
        "duplicate_module_definition", "missing_include_dependency",
        "symlink_escapes_corpus", "vhdl_only_requires_adapter",
    )
    eligible = []
    for manifest in manifests:
        origin = manifest["identity"]["origin"]
        compilation = manifest["compilation"]
        dependencies = manifest["dependencies"]
        readiness = manifest["readiness"]
        reasons = list(readiness["reasons"])
        if readiness["status"] != "NEEDS_ADAPTER":
            continue
        if compilation["top_authority"] != "explicit_config":
            continue
        if not str(compilation["filelist_authority"]).startswith("explicit:"):
            continue
        if not origin["lineage_group"]:
            continue
        if any(reason.startswith(disallowed_reason_prefixes) for reason in reasons):
            continue
        if dependencies["missing_includes"] or dependencies["unresolved_macros"]:
            continue
        if not (1 <= manifest["size"]["source_file_count"] <= 100):
            continue
        score = (
            len(dependencies["unresolved_instantiations"]),
            manifest["size"]["source_file_count"],
            manifest["size"]["source_bytes"],
            manifest["design_id"],
        )
        eligible.append((score, manifest))

    selected = []
    used_lineages: set[str] = set()
    for score, manifest in sorted(eligible, key=lambda item: item[0]):
        origin = manifest["identity"]["origin"]
        lineage = str(origin["lineage_group"])
        if lineage in used_lineages:
            continue
        used_lineages.add(lineage)
        selected.append({
            "rank": len(selected) + 1,
            "design_id": manifest["design_id"],
            "manifest_path": f"design-manifests/{manifest['design_id']}.json",
            "manifest_digest": manifest["manifest_digest"],
            "source_bundle_digest": manifest["source_bundle_digest"],
            "lineage_group": lineage,
            "lineage_status": origin["lineage_status"],
            "selection_score": {
                "unresolved_instantiation_count": score[0],
                "source_file_count": score[1],
                "source_bytes": score[2],
            },
            "status": "PROPOSED_NOT_RUN",
            "remaining_gate": "bounded_frontend_synthesis_preflight",
        })
        if len(selected) >= limit:
            break
    payload = {
        "schema": PREFLIGHT_SHORTLIST_SCHEMA,
        "selection_policy": {
            "limit": limit,
            "one_per_declared_lineage": True,
            "requires_explicit_top": True,
            "requires_explicit_filelist": True,
            "requires_no_known_include_or_macro_gap": True,
            "prior_flow_metadata_is_not_authority": True,
        },
        "eligible_count": len(eligible),
        "selected_count": len(selected),
        "selected": selected,
        "authority": "inventory proposal only; no frontend, synthesis, flow, or repair readiness granted",
    }
    payload["shortlist_digest"] = _digest(payload)
    return payload


def build_research_inventory(
    *,
    corpus_root: str | Path,
    output: str | Path,
    acquisition_script: str | Path | None = None,
) -> dict[str, Any]:
    """Build a content-addressed read-only inventory outside the source corpus."""
    corpus = Path(corpus_root).expanduser().resolve()
    if not corpus.is_dir():
        raise ResearchInventoryError(f"corpus root is missing: {corpus}")
    destination = Path(output).expanduser().resolve()
    if _inside(destination, corpus):
        raise ResearchInventoryError("inventory output must be outside the corpus root")
    if destination.exists():
        raise ResearchInventoryError(f"refusing to overwrite inventory: {destination}")
    if acquisition_script is None:
        repo = Path(__file__).resolve().parents[3]
        parser_path = repo / "r2g-skills/rtl-acquire/scripts/acquire/discover_download_candidates.py"
    else:
        parser_path = Path(acquisition_script).expanduser().resolve()
    parser = _load_acquisition_parser(parser_path)
    parser_binding = {
        "adapter": "rtl-acquire-discovery-read-only",
        "source_path": str(parser_path),
        "sha256": _sha256_file(parser_path),
        "functions": [
            "extract_module_defs", "extract_instantiated_modules", "collect_defines",
            "extract_macro_instantiations", "resolve_macro_refs",
        ],
        "side_effects_enabled": False,
    }

    staging = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    if staging.exists():
        raise ResearchInventoryError(f"inventory staging already exists: {staging}")
    before = _snapshot(corpus)
    staging.mkdir(parents=True)
    try:
        files, links, walk_errors = _walk(corpus)
        projects, exclusions = _project_roots(corpus, files)
        for error in walk_errors:
            exclusions.append({"path": ".", "reason": error, "scope": "corpus"})
        for link in links:
            if link["escapes_corpus"]:
                exclusions.append({
                    "path": link["path"],
                    "reason": "symlink_escapes_corpus_not_followed",
                    "scope": "path",
                })

        manifests: list[dict[str, Any]] = []
        design_ids: set[str] = set()
        for project in projects:
            project_files = [path for path in files if path.is_relative_to(project)]
            manifest = _manifest(
                project, corpus, project_files, links, parser, parser_binding
            )
            if manifest["design_id"] in design_ids:
                suffix = hashlib.sha256(manifest["project_path"].encode()).hexdigest()[:8]
                manifest["design_id"] = f"{manifest['design_id']}-{suffix}"
                unsigned = dict(manifest)
                unsigned.pop("manifest_digest", None)
                manifest["manifest_digest"] = _digest(unsigned)
            design_ids.add(manifest["design_id"])
            manifests.append(manifest)
        manifests.sort(key=lambda row: row["design_id"])

        digest_groups: dict[str, list[str]] = defaultdict(list)
        module_projects: dict[str, list[str]] = defaultdict(list)
        for manifest in manifests:
            digest_groups[manifest["source_bundle_digest"]].append(manifest["design_id"])
            for module in manifest["dependencies"]["module_definitions"]:
                module_projects[module].append(manifest["design_id"])
        duplicate_groups = [
            {"source_bundle_digest": digest, "design_ids": sorted(ids)}
            for digest, ids in sorted(digest_groups.items()) if len(ids) > 1
        ]
        shared_ip = [
            {"module": module, "design_ids": sorted(set(ids))}
            for module, ids in sorted(module_projects.items()) if len(set(ids)) > 1
        ]
        duplicate_by_design: dict[str, tuple[str, list[str]]] = {}
        for group in duplicate_groups:
            ids = list(group["design_ids"])
            for design_id in ids:
                duplicate_by_design[design_id] = (
                    str(group["source_bundle_digest"]),
                    [item for item in ids if item != design_id],
                )
        for manifest in manifests:
            duplicate = duplicate_by_design.get(manifest["design_id"])
            if duplicate:
                manifest["identity"]["fork_or_derived_relation"] = {
                    "status": "identical_source_bundle",
                    "source_bundle_digest": duplicate[0],
                    "peer_design_ids": duplicate[1],
                }
            unsigned = dict(manifest)
            unsigned.pop("manifest_digest", None)
            manifest["manifest_digest"] = _digest(unsigned)
            _write_json(
                staging / "design-manifests" / f"{manifest['design_id']}.json",
                manifest,
            )

        for manifest in manifests:
            if manifest["readiness"]["status"] == "UNSUPPORTED_CURRENT_PROFILE":
                exclusions.append({
                    "path": manifest["project_path"],
                    "design_id": manifest["design_id"],
                    "reason": "unsupported_current_profile",
                    "detail": manifest["readiness"]["reasons"],
                    "scope": "design",
                })

        shortlist = _preflight_shortlist(manifests)
        _write_json(staging / "preflight-shortlist.json", shortlist)
        first_external = shortlist["selected"][0] if shortlist["selected"] else None
        pilot_entry = {
            "schema": "tehm-research-pilot-entry-proposal-v1",
            "status": "BLOCKED_PENDING_OFFICIAL_CONTROL" if first_external else "BLOCKED_NO_EXTERNAL_CANDIDATE",
            "required_composition": {"official_control_count": 1, "external_design_count": 1},
            "official_control": None,
            "external_design": first_external,
            "blockers": (["official_control_manifest_not_bound"] if first_external
                         else ["external_design_not_selected", "official_control_manifest_not_bound"]),
            "note": "inventory cannot relabel a server design as an official control",
        }
        pilot_entry["proposal_digest"] = _digest(pilot_entry)
        _write_json(staging / "pilot-entry-proposal.json", pilot_entry)
        _write_json(staging / "shared-ip.json", {
            "schema": "tehm-research-shared-ip-inventory-v1",
            "duplicate_source_groups": duplicate_groups,
            "shared_module_names": shared_ip,
            "note": "name overlap is screening evidence, not proof of shared lineage",
        })
        exclusions = sorted(exclusions, key=lambda row: (
            str(row.get("path")), str(row.get("reason"))))
        _write_json(staging / "exclusions.json", {
            "schema": EXCLUSION_SCHEMA,
            "entries": exclusions,
            "entries_digest": _digest(exclusions),
        })
        _write_csv(staging / "candidates.csv", manifests)

        after = _snapshot(corpus)
        unchanged = before == after
        if not unchanged:
            raise ResearchInventoryError("corpus changed while inventory was running")
        counts = Counter(manifest["readiness"]["status"] for manifest in manifests)
        inventory = {
            "schema": INVENTORY_SCHEMA,
            "corpus_root": str(corpus),
            "corpus_read_only": True,
            "corpus_snapshot_before": before,
            "corpus_snapshot_after": after,
            "corpus_unchanged": unchanged,
            "parser_binding": parser_binding,
            "candidate_count": len(manifests),
            "exclusion_count": len(exclusions),
            "readiness_counts": dict(sorted(counts.items())),
            "designs": [{
                "design_id": manifest["design_id"],
                "manifest_path": f"design-manifests/{manifest['design_id']}.json",
                "manifest_digest": manifest["manifest_digest"],
                "source_bundle_digest": manifest["source_bundle_digest"],
                "readiness": manifest["readiness"]["status"],
            } for manifest in manifests],
            "duplicate_source_group_count": len(duplicate_groups),
            "shared_module_name_count": len(shared_ip),
            "preflight_shortlist": {
                "path": "preflight-shortlist.json",
                "digest": shortlist["shortlist_digest"],
                "selected_count": shortlist["selected_count"],
            },
            "pilot_entry_proposal": {
                "path": "pilot-entry-proposal.json",
                "status": pilot_entry["status"],
                "digest": pilot_entry["proposal_digest"],
            },
            "authority": {
                "inventory_only": True,
                "flow_readiness_granted": False,
                "repair_readiness_granted": False,
                "stub_generation": False,
                "learner_writes": False,
            },
        }
        inventory["inventory_digest"] = _digest(inventory)
        _write_json(staging / "inventory.json", inventory)
        _write_json(staging / "artifact-manifest.json", _artifact_manifest(staging))
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_research_inventory(destination)


def _checkout_binding(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args], check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ResearchInventoryError("cannot inspect adapter authority checkout") from exc
        if result.returncode != 0:
            raise ResearchInventoryError("adapter authority root is not a readable Git checkout")
        return result.stdout.strip()

    status = run("status", "--porcelain=v1")
    return {
        "root": str(root),
        "git_head": run("rev-parse", "HEAD"),
        "git_dirty": bool(status),
        "git_status_sha256": "sha256:" + hashlib.sha256(status.encode()).hexdigest(),
    }


def _adapter_authorities(
    *,
    spec: Mapping[str, Any],
    authority: Path,
    corpus: Path,
    source_inventory: Mapping[str, Any],
    inventory_digest: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Resolve source and support authority without conflating their roles."""
    checkout = _checkout_binding(authority)
    schema = spec.get("schema")
    if schema == ADAPTER_SPEC_SCHEMA:
        expected = spec.get("authority_checkout") or {}
        if checkout["git_head"] != expected.get("git_head"):
            raise ResearchInventoryError("adapter authority Git HEAD mismatch")
        if expected.get("require_clean") is not True or checkout["git_dirty"]:
            raise ResearchInventoryError(
                "adapter authority checkout must be declared and observed clean"
            )
        subtree = PurePosixPath(str(expected.get("source_subtree") or ""))
        if subtree.is_absolute() or not subtree.parts or ".." in subtree.parts:
            raise ResearchInventoryError("adapter authority source_subtree is unsafe")
        if (authority / Path(*subtree.parts)).resolve() != corpus:
            raise ResearchInventoryError(
                "adapter authority subtree does not match inventory corpus"
            )
        source_binding = {
            "kind": "git_checkout_subtree",
            "root": str(authority),
            "git_head": checkout["git_head"],
            "source_subtree": subtree.as_posix(),
            "inventory_digest": inventory_digest,
        }
        return checkout, source_binding, True

    if schema != ADAPTER_SPEC_V2_SCHEMA:
        raise ResearchInventoryError("inventory adapter spec schema mismatch")
    expected_support = spec.get("support_authority") or {}
    if expected_support.get("kind") != "git_checkout":
        raise ResearchInventoryError("v2 support authority must be a Git checkout")
    if checkout["git_head"] != expected_support.get("git_head"):
        raise ResearchInventoryError("adapter support Git HEAD mismatch")
    if expected_support.get("require_clean") is not True or checkout["git_dirty"]:
        raise ResearchInventoryError(
            "adapter support checkout must be declared and observed clean"
        )
    expected_source = spec.get("source_authority") or {}
    snapshot = source_inventory.get("corpus_snapshot_after") or {}
    if (
        expected_source.get("kind") != "inventory_snapshot"
        or Path(str(expected_source.get("corpus_root") or "")).resolve() != corpus
        or expected_source.get("inventory_digest") != inventory_digest
        or expected_source.get("entries_digest") != snapshot.get("entries_digest")
    ):
        raise ResearchInventoryError("v2 source inventory authority mismatch")
    source_binding = {
        "kind": "inventory_snapshot",
        "corpus_root": str(corpus),
        "inventory_digest": inventory_digest,
        "entries_digest": snapshot.get("entries_digest"),
        "entry_count": snapshot.get("entry_count"),
        "git_identity": "unavailable",
    }
    return checkout, source_binding, False


def bind_research_inventory_adapters(
    *,
    inventory: str | Path,
    adapter_spec: str | Path,
    authority_root: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    """Bind explicit, non-mutating adapters to selected frozen designs."""
    source_root = Path(inventory).expanduser().resolve()
    checked = verify_research_inventory(source_root)
    source_inventory = _load_json(source_root / "inventory.json", "source inventory")
    spec_path = Path(adapter_spec).expanduser().resolve()
    spec = _load_json(spec_path, "adapter spec")
    authority = Path(authority_root).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    corpus = Path(str(source_inventory["corpus_root"])).resolve()
    if destination.exists():
        raise ResearchInventoryError(f"refusing to overwrite adapted inventory: {destination}")
    if _inside(destination, corpus):
        raise ResearchInventoryError("adapted inventory output must be outside the corpus root")
    if spec.get("source_inventory_digest") != checked["inventory_digest"]:
        raise ResearchInventoryError("adapter spec source inventory digest mismatch")
    checkout, source_authority, replace_source_identity = _adapter_authorities(
        spec=spec,
        authority=authority,
        corpus=corpus,
        source_inventory=source_inventory,
        inventory_digest=checked["inventory_digest"],
    )

    design_index = {
        row.get("design_id"): row for row in source_inventory.get("designs") or []
        if isinstance(row, Mapping)
    }
    requested = spec.get("designs")
    if not isinstance(requested, list) or not requested:
        raise ResearchInventoryError("adapter spec must select at least one design")
    ids = [row.get("design_id") for row in requested if isinstance(row, Mapping)]
    if len(ids) != len(requested) or any(type(item) is not str for item in ids):
        raise ResearchInventoryError("adapter design entries are invalid")
    if len(ids) != len(set(ids)):
        raise ResearchInventoryError("adapter design IDs must be unique")

    staging = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    if staging.exists():
        raise ResearchInventoryError(f"adapter staging already exists: {staging}")
    staging.mkdir(parents=True)
    manifests: list[dict[str, Any]] = []
    try:
        shutil.copy2(spec_path, staging / "adapter-spec.json")
        _write_json(staging / "bindings" / "source-inventory.json", {
            "schema": "tehm-research-adapter-source-binding-v1",
            "path": str(source_root),
            "inventory_digest": checked["inventory_digest"],
            "artifact_manifest_digest": checked["artifact_manifest_digest"],
        })
        for entry in requested:
            design_id = str(entry["design_id"])
            index_entry = design_index.get(design_id)
            if not isinstance(index_entry, Mapping):
                raise ResearchInventoryError(f"adapter design is absent from inventory: {design_id}")
            manifest = _load_json(
                source_root / str(index_entry["manifest_path"]), "source design manifest"
            )
            available = {row.get("path") for row in manifest.get("source_files") or []
                         if isinstance(row, Mapping)}
            filelist = entry.get("ordered_filelist")
            if not isinstance(filelist, list) or not filelist:
                raise ResearchInventoryError("adapter ordered_filelist is missing")
            for value in filelist:
                relative = PurePosixPath(str(value))
                if relative.is_absolute() or ".." in relative.parts or relative.as_posix() not in available:
                    raise ResearchInventoryError(
                        f"adapter filelist is not source-bound: {design_id}:{value}"
                    )
            top = entry.get("top_module")
            definitions = (manifest.get("dependencies") or {}).get("module_definitions") or {}
            if type(top) is not str or top not in definitions:
                raise ResearchInventoryError(f"adapter top is not defined: {design_id}:{top}")
            include_dirs = entry.get("include_dirs") or []
            if not isinstance(include_dirs, list):
                raise ResearchInventoryError("adapter include_dirs must be a list")
            for value in include_dirs:
                relative = PurePosixPath(str(value))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ResearchInventoryError("adapter include_dir is unsafe")
                if not (corpus / manifest["project_path"] / Path(*relative.parts)).is_dir():
                    raise ResearchInventoryError("adapter include_dir is missing")

            support = []
            for value in entry.get("support_files") or []:
                relative = PurePosixPath(str(value))
                if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                    raise ResearchInventoryError("adapter support file path is unsafe")
                source = authority / Path(*relative.parts)
                if not source.is_file():
                    raise ResearchInventoryError(f"adapter support file is missing: {relative}")
                frozen = (Path("bindings") / "support" / design_id
                          / Path(*relative.parts))
                target = staging / frozen
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                support.append({
                    "source_path": relative.as_posix(),
                    "frozen_path": frozen.as_posix(),
                    "bytes": source.stat().st_size,
                    "sha256": _sha256_file(source),
                })

            compilation = dict(manifest["compilation"])
            compilation.update({
                "top_module": top,
                "top_authority": "explicit_adapter_spec",
                "top_evidence": [{"source": "adapter-spec.json", "value": top}],
                "ordered_filelist": [str(value) for value in filelist],
                "filelist_authority": "explicit:adapter-spec.json",
                "include_dirs": [str(value) for value in include_dirs],
                "defines": dict(entry.get("defines") or {}),
                "top_parameters": dict(entry.get("top_parameters") or {}),
            })
            manifest["compilation"] = compilation
            manifest["identity"] = dict(manifest["identity"])
            if replace_source_identity:
                manifest["identity"]["git"] = checkout
                manifest["identity"]["origin"] = {
                    "source_urls": [],
                    "declared_repository": "OpenROAD-flow-scripts",
                    "lineage_group": f"official-orfs:{checkout['git_head']}:{design_id}",
                    "lineage_status": "official_checkout_bound",
                    "catalog_bucket": design_id,
                    "catalog_bucket_is_not_lineage": True,
                }
            reasons = [reason for reason in manifest["readiness"]["reasons"]
                       if reason != "ordered_filelist_unverified"]
            manifest["readiness"] = {
                **manifest["readiness"],
                "status": "NEEDS_ADAPTER",
                "reasons": sorted(set(reasons + ["adapter_bound_pending_frontend_preflight"])),
                "ready_flow": False,
                "ready_rtl_repair": False,
            }
            manifest["adapter_binding"] = {
                "adapter_id": spec.get("adapter_id"),
                "role": entry.get("role"),
                "source_manifest_digest": index_entry["manifest_digest"],
                "source_authority": source_authority,
                "authority_checkout": checkout,
                "support_files": support,
                "flow_binding": dict(entry.get("flow_binding") or {}),
                "logic_changes": [],
                "stub_generated": False,
            }
            unsigned_manifest = dict(manifest)
            unsigned_manifest.pop("manifest_digest", None)
            manifest["manifest_digest"] = _digest(unsigned_manifest)
            manifests.append(manifest)
            _write_json(staging / "design-manifests" / f"{design_id}.json", manifest)

        manifests.sort(key=lambda row: row["design_id"])
        _write_csv(staging / "candidates.csv", manifests)
        counts = Counter(row["readiness"]["status"] for row in manifests)
        adapted = {
            "schema": INVENTORY_SCHEMA,
            "corpus_root": str(corpus),
            "corpus_read_only": True,
            "corpus_snapshot_before": source_inventory["corpus_snapshot_before"],
            "corpus_snapshot_after": source_inventory["corpus_snapshot_after"],
            "corpus_unchanged": True,
            "parser_binding": source_inventory["parser_binding"],
            "candidate_count": len(manifests),
            "exclusion_count": 0,
            "readiness_counts": dict(sorted(counts.items())),
            "designs": [{
                "design_id": row["design_id"],
                "manifest_path": f"design-manifests/{row['design_id']}.json",
                "manifest_digest": row["manifest_digest"],
                "source_bundle_digest": row["source_bundle_digest"],
                "readiness": row["readiness"]["status"],
            } for row in manifests],
            "duplicate_source_group_count": 0,
            "shared_module_name_count": 0,
            "adapter_binding": {
                "adapter_id": spec.get("adapter_id"),
                "source_inventory_digest": checked["inventory_digest"],
                "source_authority": source_authority,
                "authority_checkout": checkout,
                "logic_changes": [],
                "stub_generation": False,
            },
            "authority": {
                "inventory_only": True,
                "flow_readiness_granted": False,
                "repair_readiness_granted": False,
                "stub_generation": False,
                "learner_writes": False,
                "explicit_adapter_bound": True,
            },
        }
        adapted["inventory_digest"] = _digest(adapted)
        _write_json(staging / "inventory.json", adapted)
        _write_json(staging / "artifact-manifest.json", _artifact_manifest(staging))
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_research_inventory(destination)


def verify_research_inventory(path: str | Path) -> dict[str, Any]:
    """Verify frozen inventory artifacts and the still-read-only source corpus."""
    root = Path(path).expanduser().resolve()
    inventory = _load_json(root / "inventory.json", "research inventory")
    if inventory.get("schema") != INVENTORY_SCHEMA:
        raise ResearchInventoryError("research inventory schema mismatch")
    unsigned = dict(inventory)
    claimed_inventory_digest = unsigned.pop("inventory_digest", None)
    if claimed_inventory_digest != _digest(unsigned):
        raise ResearchInventoryError("research inventory digest mismatch")
    artifacts = _load_json(root / "artifact-manifest.json", "artifact manifest")
    if artifacts.get("schema") != ARTIFACT_MANIFEST_SCHEMA:
        raise ResearchInventoryError("inventory artifact schema mismatch")
    unsigned_artifacts = dict(artifacts)
    claimed_artifact_digest = unsigned_artifacts.pop("manifest_digest", None)
    if claimed_artifact_digest != _digest(unsigned_artifacts):
        raise ResearchInventoryError("inventory artifact manifest digest mismatch")
    files = artifacts.get("files")
    if not isinstance(files, list) or artifacts.get("files_digest") != _digest(files):
        raise ResearchInventoryError("inventory artifact file digest mismatch")
    for entry in files:
        if not isinstance(entry, Mapping):
            raise ResearchInventoryError("invalid inventory artifact entry")
        relative = PurePosixPath(str(entry.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ResearchInventoryError("unsafe inventory artifact path")
        target = root / Path(*relative.parts)
        if (not target.is_file() or target.stat().st_size != entry.get("bytes")
                or _sha256_file(target) != entry.get("sha256")):
            raise ResearchInventoryError(f"inventory artifact drifted: {relative}")

    corpus = Path(str(inventory.get("corpus_root") or "")).resolve()
    current_snapshot = _snapshot(corpus)
    if current_snapshot != inventory.get("corpus_snapshot_after"):
        raise ResearchInventoryError("source corpus drifted after inventory")
    for design in inventory.get("designs") or []:
        manifest_path = root / str(design.get("manifest_path") or "")
        manifest = _load_json(manifest_path, "design manifest")
        claimed_manifest_digest = manifest.get("manifest_digest")
        unsigned_manifest = dict(manifest)
        unsigned_manifest.pop("manifest_digest", None)
        if claimed_manifest_digest != _digest(unsigned_manifest):
            raise ResearchInventoryError("design manifest digest mismatch")
        if claimed_manifest_digest != design.get("manifest_digest"):
            raise ResearchInventoryError("inventory/design manifest binding mismatch")
        project = corpus / str(manifest.get("project_path") or "")
        source_entries = manifest.get("source_files")
        if not isinstance(source_entries, list):
            raise ResearchInventoryError("design source manifest is malformed")
        for entry in source_entries:
            relative = PurePosixPath(str(entry.get("path") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise ResearchInventoryError("unsafe design source path")
            source = project / Path(*relative.parts)
            if (not source.is_file() or source.stat().st_size != entry.get("bytes")
                    or _sha256_file(source) != entry.get("sha256")):
                raise ResearchInventoryError(
                    f"design source drifted: {manifest['design_id']}:{relative}"
                )
        if _digest(source_entries) != manifest.get("source_bundle_digest"):
            raise ResearchInventoryError("design source bundle digest mismatch")
    return {
        "valid": True,
        "inventory_digest": claimed_inventory_digest,
        "artifact_manifest_digest": claimed_artifact_digest,
        "candidate_count": inventory["candidate_count"],
        "exclusion_count": inventory["exclusion_count"],
        "readiness_counts": inventory["readiness_counts"],
        "corpus_unchanged": True,
        "corpus_root": str(corpus),
    }


__all__ = [
    "ADAPTER_SPEC_SCHEMA", "ADAPTER_SPEC_V2_SCHEMA", "ARTIFACT_MANIFEST_SCHEMA",
    "DESIGN_MANIFEST_SCHEMA", "EXCLUSION_SCHEMA",
    "INVENTORY_SCHEMA", "PREFLIGHT_SHORTLIST_SCHEMA", "ResearchInventoryError",
    "bind_research_inventory_adapters", "build_research_inventory",
    "verify_research_inventory",
]
