"""Content-addressed Research Runtime RC1 epoch freeze.

The research epoch is an experiment-layer envelope.  It binds the current
working source bytes, schema, toolchain, oracle implementation, controller,
budget, and read-only memory snapshot without granting production or memory
mutation authority.  Runtime artifacts are intentionally written outside the
repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from tehm.orfs_toolchain import load_toolchain_manifest
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
