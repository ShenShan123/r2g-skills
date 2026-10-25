"""Package passed designs and an honest inventory of the bytes delivered.

The inventory describes files captured while packaging. It does not establish
that those inputs were the ones used by earlier EDA checks.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import time

from .jobs import PASSED, JobStore
from .paths import r2g_home

REPORTS = ("drc.json", "lvs.json", "rcx.json", "route.json", "ppa.json",
           "timing_check.json", "signoff_manifest.json", "signoff_gate.json",
           "graph_dataset.json", "image_dataset.json", "verify.json",
           "fix_log.jsonl", "layout.png", "toolchain_capabilities.json", "task_contract.json")
REPAIR_REPORTS = ("agent_repair.json", "agent_repair.diff", "functional_check.json", "input_bindings.json")
LOG_STAGES = ("prepare", "synth", "size", "flow", "signoff", "repair", "graph", "verify", "preview")
# Limits apply to uncompressed bytes, including generated manifests. They bound
# individual downloads and multi-design jobs independently of compression.
MAX_FILE_BYTES = 2 * 1024 ** 3
MAX_ARCHIVE_BYTES = 8 * 1024 ** 3
MAX_MEMBERS = 50_000
MAX_DEPTH = 32


def _component(value: str) -> str:
    if (not isinstance(value, str) or not value or value in (".", "..")
            or any(c in value for c in ("/", "\\")) or any(ord(c) < 32 for c in value)):
        raise ValueError("unsafe archive path component")
    return value


def _open_relative(base: int, relative: str, *, directory=False) -> int:
    """Resolve every component against an open directory, never following links."""
    parts = relative.split("/")
    for part in parts:
        _component(part)
    parent = os.dup(base)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        return os.open(parts[-1], flags, dir_fd=parent)
    finally:
        os.close(parent)


@contextmanager
def _directory(path: Path):
    absolute = path.absolute()
    root = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        fd = _open_relative(root, absolute.as_posix().lstrip("/"), directory=True)
    finally:
        os.close(root)
    try:
        yield fd
    finally:
        os.close(fd)


def _tree(base: int, relative: str, *, budget, required=False, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("archive directory nesting exceeds limit")
    try:
        directory = _open_relative(base, relative, directory=True)
    except FileNotFoundError:
        if required:
            raise ValueError("required dataset directory is missing") from None
        return
    try:
        names = os.listdir(directory)
        budget.scanned += len(names)
        if budget.scanned > MAX_MEMBERS:
            raise ValueError("archive traversal entry count exceeds limit")
        for name in sorted(names):
            _component(name)
            mode = os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode
            member = relative + "/" + name
            if stat.S_ISDIR(mode):
                yield from _tree(base, member, budget=budget, required=True, depth=depth + 1)
            elif stat.S_ISREG(mode):
                yield member
            else:
                raise ValueError("archive accepts regular files only: " + member)
    finally:
        os.close(directory)


class _HashingReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, size):
        data = self.source.read(size)
        self.digest.update(data)
        return data


class _Archive:
    def __init__(self, tar):
        self.tar = tar
        self.size = 0
        self.names = set()
        self.scanned = 0

    def _reserve(self, name, size):
        for part in name.split("/"):
            _component(part)
        if name in self.names:
            raise ValueError("duplicate archive member: " + name)
        if size > MAX_FILE_BYTES or self.size + size > MAX_ARCHIVE_BYTES:
            raise ValueError("archive size limit exceeded")
        if len(self.names) >= MAX_MEMBERS:
            raise ValueError("archive member count exceeds limit")
        self.names.add(name)
        self.size += size
        info = tarfile.TarInfo(name)
        info.size = size
        info.mode = 0o644
        return info

    def file(self, base, relative, root, *, optional=False):
        try:
            fd = _open_relative(base, relative)
        except FileNotFoundError:
            if optional:
                return None
            raise
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            os.close(fd)
            raise ValueError("archive accepts regular files only: " + relative)
        with os.fdopen(fd, "rb") as source:
            info = self._reserve(root + "/" + relative, before.st_size)
            info.mtime = int(before.st_mtime)
            reader = _HashingReader(source)
            self.tar.addfile(info, reader)
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("file changed while packaging: " + relative)
        return {"path": relative, "size": info.size, "sha256": reader.digest.hexdigest()}

    def document(self, name, doc):
        data = json.dumps(doc, indent=1, ensure_ascii=False).encode()
        self.tar.addfile(self._reserve(name, len(data)), io.BytesIO(data))
        return {"path": name.rsplit("/", 1)[-1], "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}


def _add_design(archive: _Archive, job: dict, d: dict, root: str, *, index=None) -> None:
    members = []
    logs = []
    spec = job.get("spec") or {}
    with _directory(Path(d["project"])) as project:
        paths = []
        optional = []
        if spec.get("output") == "image":
            optional.extend("images/" + f for f in ("features.npz", "labels.npz", "image_meta.json", "verify.json"))
            paths.extend(_tree(project, "images/png", budget=archive))
        else:
            paths.extend(_tree(project, "dataset", budget=archive, required=True))
        for folder in ("rtl", "constraints"):
            paths.extend(_tree(project, folder, budget=archive))
        optional.extend("reports/" + report for report in (*REPORTS, *REPAIR_REPORTS))
        optional.append("metadata.json")
        for relative in paths:
            members.append(archive.file(project, relative, root))
        for relative in optional:
            entry = archive.file(project, relative, root, optional=True)
            if entry:
                members.append(entry)
        for stage in LOG_STAGES:
            relative = "agent_logs/" + stage + ".log"
            try:
                fd = _open_relative(project, relative)
            except FileNotFoundError:
                continue
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError("log reference is not a regular file: " + relative)
                logs.append({"path": relative, "included": False, "location": "job_workspace"})
            finally:
                os.close(fd)
    if index is not None:
        members.append(archive.document(root + "/index.json", index))
    inputs = [m for m in members if m["path"].startswith(("rtl/", "constraints/"))]
    required_reports = ("drc.json", "lvs.json", "rcx.json", "route.json", "timing_check.json",
                        "signoff_manifest.json", "signoff_gate.json")
    required_reports += (("image_dataset.json",) if spec.get("output") == "image"
                         else ("graph_dataset.json", "verify.json"))
    present = {member["path"] for member in members}
    missing_reports = ["reports/" + name for name in required_reports if "reports/" + name not in present]
    details = d.get("detail") or {}
    manifest = {
        "schema_version": 1, "job_id": job["id"], "design": d["name"], "status": d["status"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "spec": spec,
        "capture": {"authority": "packaging_time_inventory", "pre_execution_source_verification": "unverified",
                    "cross_file_snapshot": "unverified"},
        "source_snapshot_id": None, "base_commit": None, "acceptance_policy_sha256": None,
        "packaging_input_manifest_sha256": hashlib.sha256(
            json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "recorded_checks": {"signoff": details.get("signoff"), "dataset": details.get("verify")},
        "log_references": logs, "members": members,
        "missing_reports": missing_reports,
        "inventory_excludes": ["delivery_manifest.json"],
        "unverified": ["pre_execution_source_snapshot", "evidence_binding_to_packaged_inputs",
                       "acceptance_policy_version", "reviewable_change_diff", "checks_outside_recorded_scope"],
    }
    if not inputs:
        manifest["unverified"].append("input_files_missing")
    if missing_reports:
        manifest["unverified"].append("required_reports_missing")
    binding = details.get("repair_validation")
    if binding:
        from .input_snapshots import digest
        from .repair_checks import oracle_policy
        if binding.get("status") != "verified" or missing_reports:
            raise ValueError("repair delivery lacks required verified evidence")
        if digest(oracle_policy()) != binding["policy_sha256"]:
            raise ValueError("repair acceptance policy changed before packaging")
        for directory in ("rtl", "constraints"):
            recorded = sorted(({**entry, "path": entry["path"][len(directory) + 1:]}
                               for entry in inputs if entry["path"].startswith(directory + "/")),
                              key=lambda entry: entry["path"])
            if digest(recorded) != binding["snapshot"][directory]["sha256"]:
                raise ValueError("repair inputs changed between validation and packaging")
        if not all("reports/" + name in present for name in REPAIR_REPORTS):
            raise ValueError("repair delivery lacks patch, functional check or input binding")
        manifest.update(source_snapshot_id=binding["snapshot"]["sha256"],
                        acceptance_policy_sha256=binding["policy_sha256"],
                        repair_proposal_id=binding["proposal_id"])
        manifest["capture"].update(authority="bound_execution_inputs",
                                   pre_execution_source_verification="verified",
                                   cross_file_snapshot="matched_by_content_hash")
        manifest["unverified"] = ["base_commit_not_bound", "checks_outside_recorded_scope"]
    archive.document(root + "/delivery_manifest.json", manifest)


@contextmanager
def _publish(out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    with _directory(out.parent):
        pass  # refuse output paths that traverse a symlink
    if out.is_symlink() or (out.exists() and not out.is_file()):
        raise ValueError("archive destination is not a regular file")
    fd, temporary = tempfile.mkstemp(prefix="." + out.name + ".", suffix=".tmp", dir=out.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            with tarfile.open(fileobj=stream, mode="w:gz") as tar:
                yield _Archive(tar)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)


def _design_summary(d):
    details = d.get("detail") or {}
    return {"name": d["name"], "sizing": details.get("sizing"),
            "signoff": details.get("signoff"), "verify": details.get("verify")}


def package_job(store: JobStore, jid: str, partial: bool = False) -> Path:
    """Package passed designs; partial snapshots never replace the final archive."""
    _component(jid)
    job = store.job(jid)
    if not job:
        raise ValueError("job does not exist")
    spec = job.get("spec") or {}
    params = spec.get("params") or {}
    root = "r2g_dataset_" + jid
    out = r2g_home() / "datasets" / (root + ("_partial" if partial else "") + ".tar.gz")
    index = {"job": jid, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "partial": partial,
             "platform": spec.get("platform"), "output": spec.get("output", "graph"),
             "params_source": "custom" if params else "r2g_default", "params": params, "designs": []}
    with _publish(out) as archive:
        for d in job["designs"]:
            if d["status"] == PASSED:
                _add_design(archive, job, d, root + "/" + _component(d["name"]))
                index["designs"].append(_design_summary(d))
        archive.document(root + "/index.json", index)
    return out


def package_design(store: JobStore, jid: str, name: str) -> Path:
    _component(jid)
    _component(name)
    job, design = store.job(jid), store.design(jid, name)
    if not job or not design or design["status"] != PASSED:
        raise ValueError("design has not passed")
    out = r2g_home() / "datasets" / "per_design" / jid / ("r2g_%s_%s.tar.gz" % (jid, name))
    with _publish(out) as archive:
        _add_design(archive, job, design, "r2g_" + name,
                    index={"job": jid, "designs": [_design_summary(design)]})
    return out
