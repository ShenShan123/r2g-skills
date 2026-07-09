from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from .io_utils import now_iso, write_json


def file_fingerprint(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    digest = ""
    if path.is_file():
        try:
            digest = sha256(path.read_bytes()).hexdigest()[:16]
        except Exception:
            digest = ""
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha16": digest,
    }


def snapshot_global_targets(targets: dict[str, Path]) -> dict[str, dict]:
    return {name: file_fingerprint(path) for name, path in targets.items()}


def changed_global_targets(before: dict[str, dict], after: dict[str, dict]) -> dict[str, dict]:
    changed: dict[str, dict] = {}
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            changed[name] = {"before": before.get(name, {}), "after": after.get(name, {})}
    return changed


def write_run_manifest(run_dir: Path, latest_path: Path, run_manifest: dict) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    started = str(run_manifest.get("started_at", now_iso())).replace(":", "").replace("-", "")
    manifest_path = run_dir / f"run_manifest_{started}.json"
    write_json(manifest_path, run_manifest)
    write_json(latest_path, run_manifest)
    return manifest_path
