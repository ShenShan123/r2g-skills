"""Deterministic TEHM evidence-bundle export/import.

The sync format is deliberately a directory rather than a tarball: every file
has a stable POSIX path, byte hash and size in ``bundle_manifest.json``.  The
manifest itself is canonical JSON and contains no host-specific source paths.
This makes export -> import -> export byte-stable and lets a frozen bundle be
verified without opening a writable TEHM database.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import Iterable


SYNC_VERSION = "tehm-sync-v1"
MANIFEST_NAME = "bundle_manifest.json"


def canonical_json(value: object) -> bytes:
    """Return the one canonical JSON representation used by the bundle."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_rel(value: str | Path) -> str:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or not raw or raw == ".":
        raise ValueError(f"unsafe bundle path: {value!r}")
    return path.as_posix()


def _entry(path: Path, relative: str) -> dict:
    return {"path": _safe_rel(relative), "sha256": sha256_file(path),
            "size": path.stat().st_size,
            "mode": stat.S_IMODE(path.stat().st_mode)}


def _copy_bytes(src: Path, dst: Path, mode: int | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    if mode is not None:
        dst.chmod(mode)


def _iter_files(root: Path) -> Iterable[tuple[Path, str]]:
    if not root.is_dir():
        raise FileNotFoundError(f"bundle source directory not found: {root}")
    for path in sorted((p for p in root.rglob("*") if p.is_file()),
                       key=lambda p: p.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"symlinks are not allowed in evidence bundles: {path}")
        yield path, path.relative_to(root).as_posix()


def _bundle_digest(version: str, metadata: dict, entries: list[dict]) -> str:
    payload = {"version": version, "metadata": metadata, "entries": entries}
    return sha256_bytes(canonical_json(payload))


def _manifest_digest(manifest: dict) -> str:
    payload = dict(manifest)
    payload.pop("manifest_digest", None)
    return sha256_bytes(canonical_json(payload))


def _write_manifest(root: Path, manifest: dict) -> None:
    manifest = dict(manifest)
    manifest["manifest_digest"] = _manifest_digest(manifest)
    (root / MANIFEST_NAME).write_bytes(canonical_json(manifest))


def _build_manifest(metadata: dict, entries: list[dict]) -> dict:
    entries = sorted(entries, key=lambda item: item["path"])
    if len({item["path"] for item in entries}) != len(entries):
        raise ValueError("duplicate bundle paths")
    manifest = {
        "version": SYNC_VERSION,
        "metadata": metadata,
        "entries": entries,
    }
    manifest["bundle_digest"] = _bundle_digest(SYNC_VERSION, metadata, entries)
    return manifest


def export_bundle(*, output: Path, db_path: Path, artifact_root: Path,
                  evidence_files: Iterable[tuple[Path, str]] = (),
                  metadata: dict | None = None, overwrite: bool = False) -> dict:
    """Export a TEHM DB, artifact store and named evidence files.

    ``evidence_files`` supplies ``(source_path, bundle_relative_path)`` pairs.
    Host paths are used only to read bytes and never appear in the manifest.
    """
    output = Path(output)
    if output.exists():
        if not overwrite:
            raise FileExistsError(f"refusing to overwrite bundle: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"TEHM database not found: {db_path}")
    entries: list[dict] = []
    db_dest = output / "closed_loop" / "tehm.sqlite"
    _copy_bytes(db_path, db_dest)
    entries.append(_entry(db_dest, "closed_loop/tehm.sqlite"))
    for source, rel in _iter_files(Path(artifact_root)):
        rel = _safe_rel(Path("closed_loop/artifacts") / rel)
        dest = output / rel
        _copy_bytes(source, dest, stat.S_IMODE(source.stat().st_mode))
        entries.append(_entry(dest, rel))
    seen = {item["path"] for item in entries}
    for source, rel in evidence_files:
        source = Path(source)
        rel = _safe_rel(rel)
        if rel in seen or rel == MANIFEST_NAME:
            raise ValueError(f"evidence path collides with bundle path: {rel}")
        if not source.is_file():
            raise FileNotFoundError(f"evidence file not found: {source}")
        dest = output / rel
        _copy_bytes(source, dest, stat.S_IMODE(source.stat().st_mode))
        entries.append(_entry(dest, rel))
        seen.add(rel)
    manifest = _build_manifest(dict(metadata or {}), entries)
    _write_manifest(output, manifest)
    return json.loads((output / MANIFEST_NAME).read_text())


def _read_manifest(root: Path) -> dict:
    path = Path(root) / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"bundle manifest not found: {path}")
    manifest = json.loads(path.read_text())
    if manifest.get("version") != SYNC_VERSION:
        raise ValueError(f"unsupported bundle version: {manifest.get('version')!r}")
    if manifest.get("manifest_digest") != _manifest_digest(manifest):
        raise ValueError("bundle manifest digest mismatch")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or entries != sorted(entries, key=lambda item: item["path"]):
        raise ValueError("bundle entries are not deterministically ordered")
    if manifest.get("bundle_digest") != _bundle_digest(
            manifest["version"], manifest.get("metadata") or {}, entries):
        raise ValueError("bundle digest mismatch")
    return manifest


def verify_bundle(root: Path) -> dict:
    """Verify manifest, hashes, path safety and exact file membership."""
    root = Path(root)
    try:
        manifest = _read_manifest(root)
        expected = {MANIFEST_NAME}
        for item in manifest["entries"]:
            rel = _safe_rel(item["path"])
            expected.add(rel)
            path = root / rel
            if not path.is_file():
                raise FileNotFoundError(f"bundle entry missing: {rel}")
            if path.stat().st_size != item["size"]:
                raise ValueError(f"bundle size mismatch: {rel}")
            if stat.S_IMODE(path.stat().st_mode) != item.get("mode", 0o644):
                raise ValueError(f"bundle mode mismatch: {rel}")
            if sha256_file(path) != item["sha256"]:
                raise ValueError(f"bundle hash mismatch: {rel}")
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        if actual != expected:
            raise ValueError(f"unexpected bundle files: {sorted(actual - expected)[:5]}")
        return {"ok": True, "detail":
                f"{len(manifest['entries'])} entries verified; manifest and bundle digests match",
                "manifest": manifest}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "detail": str(exc)}


def import_bundle(*, bundle: Path, output: Path, overwrite: bool = False) -> dict:
    """Verify and unpack a bundle into a new directory."""
    bundle = Path(bundle)
    checked = verify_bundle(bundle)
    if not checked["ok"]:
        raise ValueError(f"cannot import invalid bundle: {checked['detail']}")
    output = Path(output)
    if output.exists():
        if not overwrite:
            raise FileExistsError(f"refusing to overwrite import directory: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for item in [{"path": MANIFEST_NAME, "mode": 0o644}] + checked["manifest"]["entries"]:
        _copy_bytes(bundle / item["path"], output / item["path"], item.get("mode"))
    return checked["manifest"]


def reexport_bundle(*, source_bundle: Path, output: Path,
                    overwrite: bool = False) -> dict:
    """Re-export an imported bundle using its canonical manifest and bytes."""
    source_bundle = Path(source_bundle)
    checked = verify_bundle(source_bundle)
    if not checked["ok"]:
        raise ValueError(f"cannot re-export invalid bundle: {checked['detail']}")
    output = Path(output)
    if output.exists():
        if not overwrite:
            raise FileExistsError(f"refusing to overwrite re-export: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for item in [{"path": MANIFEST_NAME, "mode": 0o644}] + checked["manifest"]["entries"]:
        _copy_bytes(source_bundle / item["path"], output / item["path"], item.get("mode"))
    # Re-serialise the manifest to prove the canonical encoder is stable.
    manifest = _read_manifest(output)
    _write_manifest(output, manifest)
    return manifest


__all__ = [
    "MANIFEST_NAME", "SYNC_VERSION", "canonical_json", "export_bundle",
    "import_bundle", "reexport_bundle", "verify_bundle",
]
