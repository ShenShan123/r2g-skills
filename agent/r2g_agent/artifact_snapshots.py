"""Bound dataset bytes without loading tensors or following filesystem links."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat

from .input_snapshots import digest

MAX_FILE_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 8 * 1024 ** 3
MAX_ENTRIES = 50_000
CHUNK_BYTES = 1024 * 1024


def selected(path: str, kind: str) -> bool:
    if kind == "graph":
        return path.startswith("dataset/")
    if kind == "image":
        return path in {"images/features.npz", "images/labels.npz", "images/image_meta.json"} or path.startswith("images/png/")
    raise ValueError("unsupported dataset kind")


def from_entries(entries: list[dict], kind: str) -> dict:
    files = sorted((entry for entry in entries if selected(entry["path"], kind)), key=lambda entry: entry["path"])
    if not files or len({entry["path"] for entry in files}) != len(files):
        raise ValueError("dataset artifacts missing or duplicated")
    if kind == "image" and not {"images/features.npz", "images/labels.npz", "images/image_meta.json"}.issubset(
            entry["path"] for entry in files):
        raise ValueError("image artifacts incomplete")
    return {"schema_version": 1, "kind": kind, "files": files, "sha256": digest(files)}


@contextmanager
def _directory(path: Path):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.absolute().parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _file(base: int, name: str, relative: str, remaining: int) -> dict:
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=base)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > min(MAX_FILE_BYTES, remaining):
            raise ValueError("artifact type or byte limit refused")
        h, size = hashlib.sha256(), 0
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            if size > min(MAX_FILE_BYTES, remaining):
                raise ValueError("artifact byte limit exceeded")
            h.update(chunk)
        after = os.fstat(stream.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after) or size != before.st_size:
            raise ValueError("artifact changed during capture")
    return {"path": relative, "size": size, "sha256": h.hexdigest()}


def capture(project: Path, kind: str) -> dict:
    entries, total, scanned = [], 0, 0

    def visit(fd, prefix, depth=0):
        nonlocal total, scanned
        if depth > 32:
            raise ValueError("artifact nesting limit exceeded")
        before = os.fstat(fd)
        for entry in os.scandir(fd):
            scanned += 1
            if scanned > MAX_ENTRIES:
                raise ValueError("artifact entry limit exceeded")
            relative = prefix + entry.name
            mode = os.stat(entry.name, dir_fd=fd, follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    visit(child, relative + "/", depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(mode):
                value = _file(fd, entry.name, relative, MAX_TOTAL_BYTES - total)
                entries.append(value)
                total += value["size"]
            else:
                raise ValueError("artifacts must be regular files and directories")
        after = os.fstat(fd)
        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("artifact membership changed during capture")

    if kind == "graph":
        with _directory(project / "dataset") as fd:
            visit(fd, "dataset/")
    elif kind == "image":
        with _directory(project / "images") as fd:
            for name in ("features.npz", "labels.npz", "image_meta.json"):
                entry = _file(fd, name, "images/" + name, MAX_TOTAL_BYTES - total)
                entries.append(entry)
                total += entry["size"]
            try:
                child = os.open("png", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                pass
            else:
                try:
                    visit(child, "images/png/")
                finally:
                    os.close(child)
    return from_entries(entries, kind)


def verifier_path(kind: str) -> str:
    return "images/verify.json" if kind == "image" else "reports/verify.json"


def _observe(project, kind):
    try:
        return capture(project, kind)
    except (OSError, ValueError) as exc:
        return {"unavailable": type(exc).__name__}


def begin(project: Path, kind: str) -> dict:
    receipt = {"schema_version": 1, "kind": kind, "stage": "verify" if kind == "graph" else "graph",
               "scope": "unchanged_during_verifier" if kind == "graph" else "post_generation_and_internal_verify",
               "before": _observe(project, kind) if kind == "graph" else None,
               "after": None, "verifier": None, "exit_code": None}
    # An earlier green report cannot certify a command that produced no report.
    relative = Path(verifier_path(kind))
    try:
        with _directory(project / relative.parent) as fd:
            os.unlink(relative.name, dir_fd=fd)
    except FileNotFoundError:
        pass
    return receipt


def finish(project: Path, kind: str, receipt: dict, execution_id: str, exit_code: int) -> dict:
    relative = Path(verifier_path(kind))
    try:
        with _directory(project / relative.parent) as fd:
            verifier = _file(fd, relative.name, relative.as_posix(), 8 * 1024 * 1024)
    except (OSError, ValueError) as exc:
        verifier = {"unavailable": type(exc).__name__}
    return {**receipt, "after": _observe(project, kind), "verifier": verifier,
            "execution_id": execution_id, "exit_code": exit_code}
