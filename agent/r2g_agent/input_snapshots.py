"""Content-bound input observations, read without following source symlinks."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_INPUT_FILES = 4096


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def read_tree(root: Path) -> dict[str, bytes]:
    files = {}
    total = 0
    scanned = 0

    def visit(fd, prefix="", depth=0):
        nonlocal total, scanned
        if depth > 24:
            raise ValueError("input nesting limit exceeded")
        # Enumerate lazily so a directory containing many empty subdirectories
        # cannot allocate an unbounded list before the admission check.
        for entry in os.scandir(fd):
            scanned += 1
            if scanned > MAX_INPUT_FILES:
                raise ValueError("input entry limit exceeded")
            name = entry.name
            mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    visit(child, prefix + name + "/", depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(mode):
                child = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
                with os.fdopen(child, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size + total > MAX_INPUT_BYTES:
                        raise ValueError("input byte limit or file type refused")
                    data = stream.read(MAX_INPUT_BYTES - total + 1)
                    after = os.fstat(stream.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                            after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
                        raise ValueError("input changed during capture")
                files[prefix + name] = data
                total += len(data)
                if total > MAX_INPUT_BYTES or len(files) > MAX_INPUT_FILES:
                    raise ValueError("input capture limit exceeded")
            else:
                raise ValueError("inputs must be regular files and directories")

    # Resolve every ancestor using directory descriptors as well.
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.absolute().parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        visit(current)
    finally:
        os.close(current)
    return files


def manifest(files: dict[str, bytes]) -> dict:
    entries = [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
               for name, data in sorted(files.items())]
    return {"sha256": digest(entries), "files": entries}


def capture(project: Path) -> dict:
    value = {name: manifest(read_tree(project / name)) for name in ("rtl", "constraints")}
    return {**value, "sha256": digest(value)}


def reference(snapshot: dict) -> dict:
    return {"sha256": snapshot["sha256"], "rtl_sha256": snapshot["rtl"]["sha256"],
            "constraints_sha256": snapshot["constraints"]["sha256"]}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".pending")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
