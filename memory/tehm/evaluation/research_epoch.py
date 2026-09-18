"""Content-addressed Research Runtime RC1 epoch freeze.

The research epoch is an experiment-layer envelope.  It binds the current
working source bytes, schema, toolchain, oracle implementation, controller,
budget, and read-only memory snapshot without granting production or memory
mutation authority.  Runtime artifacts are intentionally written outside the
repository.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from tehm.orfs_toolchain import load_toolchain_manifest
from tehm.schema_contract import freeze_schema_contract
from tehm.sync import verify_bundle


EPOCH_SCHEMA = "tehm-research-epoch-v1"
EVIDENCE_STATUS_SCHEMA = "tehm-research-evidence-status-v1"
ARTIFACT_MANIFEST_SCHEMA = "tehm-research-epoch-artifact-manifest-v1"
EVIDENCE_CLASSES = {
    "valid",
    "corrected",
    "diagnostic_only",
    "pending_replay",
}


class ResearchEpochError(ValueError):
    """Raised when a research epoch cannot be frozen or replayed."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
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
        raise ResearchEpochError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchEpochError(f"{label} must be a JSON object")
    return value


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResearchEpochError(f"git command failed: {' '.join(args)}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ResearchEpochError(
            f"git command failed ({' '.join(args)}): {detail}"
        )
    return result.stdout


def _source_roots(repo: Path, roots: Sequence[str]) -> tuple[str, ...]:
    if isinstance(roots, (str, bytes)) or not isinstance(roots, Sequence):
        raise ResearchEpochError("source roots must be a sequence")
    checked: list[str] = []
    for raw in roots:
        if not isinstance(raw, str) or not raw:
            raise ResearchEpochError("source root must be a non-empty string")
        posix = PurePosixPath(raw)
        if posix.is_absolute() or ".." in posix.parts or str(posix) in {"", "."}:
            raise ResearchEpochError(f"invalid source root: {raw}")
        resolved = (repo / Path(*posix.parts)).resolve()
        try:
            resolved.relative_to(repo)
        except ValueError as exc:
            raise ResearchEpochError(f"source root escapes repository: {raw}") from exc
        if not resolved.exists():
            raise ResearchEpochError(f"source root is missing: {raw}")
        checked.append(posix.as_posix())
    if len(set(checked)) != len(checked):
        raise ResearchEpochError("source roots must not contain duplicates")
    return tuple(sorted(checked))


def _listed_paths(repo: Path, roots: tuple[str, ...]) -> tuple[list[str], list[str]]:
    tracked_raw = _git(repo, "ls-files", "-z", "--", *roots)
    untracked_raw = _git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *roots,
    )
    tracked = sorted(
        item.decode("utf-8") for item in tracked_raw.split(b"\0") if item
    )
    untracked = sorted(
        item.decode("utf-8") for item in untracked_raw.split(b"\0") if item
    )
    if set(tracked) & set(untracked):
        raise ResearchEpochError("tracked and untracked source sets overlap")
    return tracked, untracked


def _entry(repo: Path, relative: str, *, tracked: bool) -> tuple[dict[str, Any], bytes | str]:
    path = (repo / relative).absolute()
    resolved_parent = path.parent.resolve()
    try:
        resolved_parent.relative_to(repo)
    except ValueError as exc:
        raise ResearchEpochError(f"source path escapes repository: {relative}") from exc
    if path.is_symlink():
        target = os.readlink(path)
        return ({
            "path": relative,
            "kind": "symlink",
            "link_target": target,
            "tracked": tracked,
            "mode": path.lstat().st_mode & 0o777,
            "sha256": "sha256:" + hashlib.sha256(target.encode()).hexdigest(),
        }, target)
    if not path.is_file():
        raise ResearchEpochError(f"listed source file is missing: {relative}")
    data = path.read_bytes()
    return ({
        "path": relative,
        "kind": "file",
        "tracked": tracked,
        "mode": path.stat().st_mode & 0o777,
        "bytes": len(data),
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
    }, data)


def _freeze_source(repo: Path, staging: Path, roots: tuple[str, ...]) -> dict[str, Any]:
    tracked, untracked = _listed_paths(repo, roots)
    entries: list[dict[str, Any]] = []
    archive_path = staging / "source" / "working-source.tar"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="w") as archive:
        for relative, is_tracked in [
            *((item, True) for item in tracked),
            *((item, False) for item in untracked),
        ]:
            path = repo / relative
            # A tracked deletion is represented by the Git diff/status, not by
            # fabricated bytes in the working-source archive.
            if not path.exists() and not path.is_symlink():
                continue
            record, payload = _entry(repo, relative, tracked=is_tracked)
            entries.append(record)
            info = tarfile.TarInfo(relative)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = int(record["mode"])
            if record["kind"] == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = str(payload)
                info.size = 0
                archive.addfile(info)
            else:
                data = bytes(payload)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    patch = _git(repo, "diff", "--binary", "HEAD", "--", *roots)
    patch_path = staging / "source" / "working-tree.patch"
    patch_path.write_bytes(patch)
    manifest = {
        "schema": "tehm-research-source-freeze-v1",
        "git_head": _git(repo, "rev-parse", "--verify", "HEAD").decode().strip(),
        "git_branch": _git(repo, "branch", "--show-current").decode().strip(),
        "dirty": bool(status),
        "git_status": status.decode("utf-8", errors="replace").splitlines(),
        "git_status_sha256": "sha256:" + hashlib.sha256(status).hexdigest(),
        "working_tree_patch_sha256": _sha256_file(patch_path),
        "source_roots": list(roots),
        "tracked_file_count": sum(bool(row["tracked"]) for row in entries),
        "untracked_file_count": sum(not bool(row["tracked"]) for row in entries),
        "entries": entries,
        "entries_digest": _digest(entries),
        "working_source_archive": "source/working-source.tar",
        "working_source_archive_sha256": _sha256_file(archive_path),
    }
    manifest["source_freeze_digest"] = _digest(manifest)
    _write_json(staging / "source" / "source-manifest.json", manifest)
    return manifest


def _copy_binding(
    source: Path,
    destination: Path,
    label: str,
    *,
    epoch_root: Path,
) -> dict[str, Any]:
    if not source.is_file():
        raise ResearchEpochError(f"{label} is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return {
        "source_path": str(source),
        "frozen_path": destination.relative_to(epoch_root).as_posix(),
        "sha256": _sha256_file(destination),
        "bytes": destination.stat().st_size,
    }


def _validate_evidence_status(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema") != EVIDENCE_STATUS_SCHEMA:
        raise ResearchEpochError("evidence status schema mismatch")
    raw = payload.get("entries")
    if not isinstance(raw, list) or not raw:
        raise ResearchEpochError("evidence status requires non-empty entries")
    rows: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ResearchEpochError("evidence status entry must be an object")
        row = dict(item)
        evidence_id = row.get("evidence_id")
        classification = row.get("classification")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ResearchEpochError("evidence status entry requires evidence_id")
        if evidence_id in identifiers:
            raise ResearchEpochError("evidence status IDs must be unique")
        if classification not in EVIDENCE_CLASSES:
            raise ResearchEpochError(
                f"evidence status classification is invalid: {classification}"
            )
        if not isinstance(row.get("basis"), str) or not row["basis"]:
            raise ResearchEpochError("evidence status entry requires basis")
        if type(row.get("selected_for_epoch", False)) is not bool:
            raise ResearchEpochError("selected_for_epoch must be boolean")
        identifiers.add(evidence_id)
        rows.append(row)
    return sorted(rows, key=lambda row: row["evidence_id"])


def _write_evidence_markdown(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    lines = [
        "# Research Epoch Evidence Status",
        "",
        "This projection is generated from the frozen evidence-status input.",
        "It does not upgrade diagnostic or pending evidence to authority.",
        "",
        "| Evidence | Classification | Selected | Basis |",
        "|---|---|---:|---|",
    ]
    for row in rows:
        basis = str(row["basis"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['evidence_id']} | {row['classification']} | "
            f"{'yes' if row.get('selected_for_epoch') else 'no'} | {basis} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _artifact_manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / "artifact-manifest.json"
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path == manifest_path:
            continue
        files.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
        })
    payload = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "files": files,
        "files_digest": _digest(files),
    }
    payload["manifest_digest"] = _digest(payload)
    return payload


def freeze_research_epoch(
    *,
    repo_root: str | Path,
    output: str | Path,
    epoch_id: str,
    toolchain_manifest: str | Path,
    memory_snapshot: str | Path,
    schema_path: str | Path,
    oracle_paths: Sequence[str | Path],
    controller_config: str | Path,
    budget_config: str | Path,
    evidence_status: str | Path,
    source_roots: Sequence[str] = ("memory", "r2g-skills"),
) -> dict[str, Any]:
    """Freeze one non-production research epoch in an external directory."""
    if not isinstance(epoch_id, str) or not epoch_id.strip():
        raise ResearchEpochError("epoch_id must be non-empty")
    repo = Path(repo_root).expanduser().resolve()
    if not (repo / ".git").exists():
        raise ResearchEpochError(f"repository is not a Git checkout: {repo}")
    destination = Path(output).expanduser().resolve()
    try:
        destination.relative_to(repo)
    except ValueError:
        pass
    else:
        raise ResearchEpochError("research epoch output must be outside the repository")
    if destination.exists():
        raise ResearchEpochError(f"refusing to overwrite research epoch: {destination}")
    roots = _source_roots(repo, source_roots)
    staging = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    if staging.exists():
        raise ResearchEpochError(f"staging directory already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        source = _freeze_source(repo, staging, roots)

        toolchain_source = Path(toolchain_manifest).expanduser().resolve()
        checked_toolchain = load_toolchain_manifest(toolchain_source)
        toolchain_binding = _copy_binding(
            toolchain_source,
            staging / "bindings" / "toolchain-manifest.json",
            "toolchain manifest",
            epoch_root=staging,
        )
        toolchain_binding["manifest_digest"] = checked_toolchain["manifest_digest"]
        toolchain_binding["binding_status"] = checked_toolchain["binding_status"]

        snapshot = Path(memory_snapshot).expanduser().resolve()
        if not snapshot.is_dir():
            raise ResearchEpochError(f"memory snapshot bundle is missing: {snapshot}")
        checked_snapshot = verify_bundle(snapshot)
        if not checked_snapshot.get("ok"):
            raise ResearchEpochError(
                f"memory snapshot verification failed: {checked_snapshot.get('detail')}"
            )
        snapshot_manifest = snapshot / "bundle_manifest.json"
        memory_binding = _copy_binding(
            snapshot_manifest,
            staging / "bindings" / "memory-bundle-manifest.json",
            "memory bundle manifest",
            epoch_root=staging,
        )
        memory_binding.update({
            "bundle_path": str(snapshot),
            "bundle_digest": checked_snapshot["manifest"].get("bundle_digest"),
            "manifest_digest": checked_snapshot["manifest"].get("manifest_digest"),
            "read_only": True,
        })

        schema_source = Path(schema_path).expanduser().resolve()
        schema_report_path = staging / "bindings" / "schema-contract.json"
        schema_report = freeze_schema_contract(
            schema_path=schema_source,
            db_path=snapshot / "closed_loop" / "tehm.sqlite",
            output=schema_report_path,
        )
        schema_binding = {
            "source_path": str(schema_source),
            "source_sha256": _sha256_file(schema_source),
            "frozen_path": "bindings/schema-contract.json",
            "receipt_id": schema_report["receipt_id"],
            "receipt_digest": schema_report["receipt_digest"],
        }

        if isinstance(oracle_paths, (str, bytes)) or not oracle_paths:
            raise ResearchEpochError("oracle_paths must be a non-empty sequence")
        oracle_bindings = []
        seen_oracles: set[Path] = set()
        for index, raw in enumerate(oracle_paths):
            source_path = Path(raw).expanduser().resolve()
            if source_path in seen_oracles:
                raise ResearchEpochError("oracle paths must not contain duplicates")
            seen_oracles.add(source_path)
            oracle_bindings.append(_copy_binding(
                source_path,
                staging / "bindings" / "oracles" / f"{index:02d}-{source_path.name}",
                "oracle source",
                epoch_root=staging,
            ))
        oracle_manifest = {
            "schema": "tehm-research-oracle-binding-v1",
            "files": oracle_bindings,
        }
        oracle_manifest["oracle_digest"] = _digest(oracle_manifest)
        _write_json(staging / "bindings" / "oracle-binding.json", oracle_manifest)

        config_bindings = {}
        for name, raw in (
            ("controller", controller_config),
            ("budget", budget_config),
            ("evidence_status", evidence_status),
        ):
            source_path = Path(raw).expanduser().resolve()
            payload = _load_json(source_path, name.replace("_", " "))
            binding = _copy_binding(
                source_path,
                staging / "bindings" / f"{name.replace('_', '-')}.json",
                name.replace("_", " "),
                epoch_root=staging,
            )
            binding["content_digest"] = _digest(payload)
            config_bindings[name] = binding

        evidence_payload = _load_json(
            Path(evidence_status).expanduser().resolve(), "evidence status"
        )
        evidence_rows = _validate_evidence_status(evidence_payload)
        _write_json(staging / "evidence-index.json", {
            "schema": EVIDENCE_STATUS_SCHEMA,
            "entries": evidence_rows,
            "entries_digest": _digest(evidence_rows),
        })
        _write_evidence_markdown(staging / "evidence-status.md", evidence_rows)

        selected_invalid = [
            row["evidence_id"] for row in evidence_rows
            if row.get("selected_for_epoch") and row["classification"] != "valid"
        ]
        controller_payload = _load_json(
            Path(controller_config).expanduser().resolve(), "controller config"
        )
        budget_payload = _load_json(
            Path(budget_config).expanduser().resolve(), "budget config"
        )
        blockers = [f"selected_evidence_not_valid:{item}" for item in selected_invalid]
        if controller_payload.get("production_authority") is not False:
            blockers.append("controller_must_disable_production_authority")
        if controller_payload.get("online_memory_update") is not False:
            blockers.append("controller_must_disable_online_memory_update")
        if budget_payload.get("candidate_limit") != 3:
            blockers.append("candidate_limit_must_be_three_for_rc1")
        for field in (
            "eda_call_limit",
            "model_call_limit",
            "model_token_limit",
            "wallclock_limit_seconds",
            "retry_limit",
        ):
            value = budget_payload.get(field)
            if type(value) is not int or value < 0:
                blockers.append(f"invalid_budget:{field}")

        epoch = {
            "schema": EPOCH_SCHEMA,
            "epoch_id": epoch_id.strip(),
            "status": "FROZEN" if not blockers else "BLOCKED",
            "research_runtime_label": "Research Runtime RC1",
            "source": source,
            "toolchain": toolchain_binding,
            "memory_snapshot": memory_binding,
            "schema_contract": schema_binding,
            "oracle": {
                "frozen_path": "bindings/oracle-binding.json",
                "oracle_digest": oracle_manifest["oracle_digest"],
            },
            "controller": config_bindings["controller"],
            "budget": config_bindings["budget"],
            "evidence_status": config_bindings["evidence_status"],
            "research_evaluation_ready": not blockers,
            "blockers": blockers,
            "authority": {
                "research_only": True,
                "production_authority": False,
                "online_memory_update": False,
                "final_test_learning": False,
                "canonical_memory_mutation": "none",
            },
        }
        epoch["epoch_digest"] = _digest(epoch)
        _write_json(staging / "research-epoch.json", epoch)
        _write_json(staging / "artifact-manifest.json", _artifact_manifest(staging))
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_research_epoch(destination)


def verify_research_epoch(path: str | Path) -> dict[str, Any]:
    """Verify frozen bytes and external read-only authority bindings."""
    root = Path(path).expanduser().resolve()
    epoch = _load_json(root / "research-epoch.json", "research epoch")
    if epoch.get("schema") != EPOCH_SCHEMA:
        raise ResearchEpochError("research epoch schema mismatch")
    claimed_epoch_digest = epoch.get("epoch_digest")
    unsigned = dict(epoch)
    unsigned.pop("epoch_digest", None)
    if claimed_epoch_digest != _digest(unsigned):
        raise ResearchEpochError("research epoch digest mismatch")
    artifact_manifest = _load_json(
        root / "artifact-manifest.json", "artifact manifest"
    )
    if artifact_manifest.get("schema") != ARTIFACT_MANIFEST_SCHEMA:
        raise ResearchEpochError("artifact manifest schema mismatch")
    unsigned_manifest = dict(artifact_manifest)
    claimed_manifest_digest = unsigned_manifest.pop("manifest_digest", None)
    if claimed_manifest_digest != _digest(unsigned_manifest):
        raise ResearchEpochError("artifact manifest digest mismatch")
    files = artifact_manifest.get("files")
    if not isinstance(files, list) or artifact_manifest.get("files_digest") != _digest(files):
        raise ResearchEpochError("artifact manifest files digest mismatch")
    for item in files:
        if not isinstance(item, Mapping):
            raise ResearchEpochError("artifact manifest entry is invalid")
        relative = PurePosixPath(str(item.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ResearchEpochError("artifact manifest path is unsafe")
        target = root / Path(*relative.parts)
        if not target.is_file() or _sha256_file(target) != item.get("sha256"):
            raise ResearchEpochError(f"frozen artifact drifted: {relative}")
        if target.stat().st_size != item.get("bytes"):
            raise ResearchEpochError(f"frozen artifact size drifted: {relative}")

    source = epoch.get("source") or {}
    archive = root / str(source.get("working_source_archive") or "")
    if not archive.is_file() or _sha256_file(archive) != source.get(
        "working_source_archive_sha256"
    ):
        raise ResearchEpochError("working source archive drifted")
    toolchain = load_toolchain_manifest(root / "bindings" / "toolchain-manifest.json")
    if toolchain.get("manifest_digest") != (epoch.get("toolchain") or {}).get(
        "manifest_digest"
    ):
        raise ResearchEpochError("toolchain binding digest mismatch")
    snapshot_path = Path(str((epoch.get("memory_snapshot") or {}).get("bundle_path") or ""))
    snapshot = verify_bundle(snapshot_path)
    if not snapshot.get("ok"):
        raise ResearchEpochError("external memory snapshot no longer verifies")
    if snapshot["manifest"].get("bundle_digest") != (
        epoch.get("memory_snapshot") or {}
    ).get("bundle_digest"):
        raise ResearchEpochError("external memory snapshot digest mismatch")
    return {
        "valid": True,
        "epoch_id": epoch["epoch_id"],
        "status": epoch["status"],
        "research_evaluation_ready": epoch["research_evaluation_ready"],
        "epoch_digest": claimed_epoch_digest,
        "artifact_manifest_digest": claimed_manifest_digest,
        "git_head": source.get("git_head"),
        "git_dirty": source.get("dirty"),
        "toolchain_manifest_digest": toolchain.get("manifest_digest"),
        "memory_bundle_digest": snapshot["manifest"].get("bundle_digest"),
        "blockers": list(epoch.get("blockers") or []),
    }
