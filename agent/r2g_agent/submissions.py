"""Submission identity includes the source bytes that the flow consumes."""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import frontend, project
from .turns import digest


class SubmissionConflict(ValueError):
    pass


def source_snapshot(sources: list[dict]) -> str:
    entries = []
    for source in sources:
        path = Path(source["path"]).resolve()
        root = path if path.is_dir() else path.parent
        files = set(project.find_hdl(path))
        manifest = root / "design.json"
        if manifest.is_file():
            files.add(manifest)
        for hdl in list(files):
            if hdl.suffix.lower() in project.HDL_SUFFIXES:
                for reference, _ in frontend.readmem_references(hdl.read_text(errors="replace")):
                    dependency, _ = frontend._resolve_data(reference, hdl, root)
                    if dependency:
                        files.add(dependency)
        for file in sorted(files):
            before = file.stat()
            checksum = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(chunk)
            after = file.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("source changed while computing submission identity")
            entries.append({"source": str(path), "file": str(file.resolve()), "sha256": checksum.hexdigest()})
    return digest(entries)
