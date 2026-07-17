#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_downloads_root,
)

DEFAULT_DOWNLOADS = default_downloads_root()
DEFAULT_CLONE_TIMEOUT_SECONDS = int(os.environ.get("R2G_ACQUIRE_CLONE_TIMEOUT_SECONDS", "900"))


def infer_dest_name(repo_url: str) -> str:
    name = repo_url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    for suffix in (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar", ".zip"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def load_manifest_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(newline="", encoding="utf-8")))


def row_source_type(row: dict[str, str]) -> str:
    explicit = (row.get("source_type") or "").strip().lower()
    if explicit:
        return explicit
    if (row.get("archive_url") or "").strip():
        return "archive"
    return "git"


def row_source_url(row: dict[str, str]) -> str:
    return (
        (row.get("source_url") or "").strip()
        or (row.get("archive_url") or "").strip()
        or (row.get("repo_url") or "").strip()
    )


def flatten_extracted_tree(extracted_root: Path, dest_dir: Path) -> None:
    entries = [p for p in extracted_root.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        source_root = entries[0]
    else:
        source_root = extracted_root
    shutil.move(str(source_root), str(dest_dir))


def clone_git_source(downloads_root: Path, row: dict[str, str], timeout_seconds: int) -> tuple[str, str, str]:
    repo_url = row_source_url(row)
    if not repo_url:
        return "", "invalid", "missing git source URL"
    dest_name = (row.get("dest_name") or infer_dest_name(repo_url)).strip()
    branch = (row.get("branch") or "").strip()
    depth = (row.get("depth") or "1").strip()
    dest_dir = downloads_root / dest_name
    if dest_dir.exists():
        return dest_name, "skipped_existing", str(dest_dir)

    cmd = ["git", "clone"]
    if depth and depth.isdigit() and int(depth) > 0:
        cmd.extend(["--depth", depth])
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([repo_url, str(dest_dir)])
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        return dest_name, "failed", f"git clone timed out after {timeout_seconds}s"
    if result.returncode == 0:
        return dest_name, "cloned", str(dest_dir)
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    detail = (result.stdout + "\n" + result.stderr).strip().splitlines()
    reason = detail[-1] if detail else f"git clone failed ({result.returncode})"
    return dest_name, "failed", reason


def clone_archive_source(downloads_root: Path, row: dict[str, str], timeout_seconds: int) -> tuple[str, str, str]:
    source_url = row_source_url(row)
    if not source_url:
        return "", "invalid", "missing archive source URL"
    dest_name = (row.get("dest_name") or infer_dest_name(source_url)).strip()
    dest_dir = downloads_root / dest_name
    if dest_dir.exists():
        return dest_name, "skipped_existing", str(dest_dir)

    downloads_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clone_repo_manifest_") as tmpdir_text:
        tmpdir = Path(tmpdir_text)
        archive_path = tmpdir / infer_dest_name(source_url)
        try:
            with urllib.request.urlopen(source_url, timeout=timeout_seconds) as response, archive_path.open("wb") as out_fh:
                shutil.copyfileobj(response, out_fh)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return dest_name, "failed", f"archive download failed: {exc}"

        extract_root = tmpdir / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)
        try:
            if zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path) as zf:
                    _safe_extract_zip(zf, extract_root)
            elif tarfile.is_tarfile(archive_path):
                with tarfile.open(archive_path) as tf:
                    _safe_extract_tar(tf, extract_root)
            else:
                return dest_name, "failed", "unsupported archive format"
            flatten_extracted_tree(extract_root, dest_dir)
        except Exception as exc:
            if dest_dir.exists():
                shutil.rmtree(dest_dir, ignore_errors=True)
            return dest_name, "failed", f"archive extract failed: {exc}"
    return dest_name, "cloned", str(dest_dir)


# --- Containment (2026-07-16 full-pipeline issue 8) --------------------------
# An UNTRUSTED archive must never write outside its extraction scratch:
# tarfile.extractall without a filter faithfully recreates `../` members,
# absolute paths, symlinks, hardlinks, and device nodes (the live traversal
# vector; zipfile sanitizes names but not symlink members). Reject every
# non-regular member and every path that escapes the target root BEFORE
# writing anything.

def _member_escapes(root: Path, name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
        return True
    target = (root / name).resolve()
    root = root.resolve()
    return not (target == root or str(target).startswith(str(root) + os.sep))


def _safe_extract_zip(zf: "zipfile.ZipFile", root: Path) -> None:
    for info in zf.infolist():
        if _member_escapes(root, info.filename):
            raise ValueError(f"unsafe zip member path: {info.filename!r}")
        # a zip member stored as a symlink (external_attr high bits) must not be
        # recreated as a link that later reads escape through
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"symlink zip member rejected: {info.filename!r}")
    zf.extractall(root)


def _safe_extract_tar(tf: "tarfile.TarFile", root: Path) -> None:
    try:
        tf.extractall(root, filter="data")     # Python 3.12+: the safe extractor
        return
    except TypeError:                          # older Python: manual validation
        pass
    members = []
    for m in tf.getmembers():
        if _member_escapes(root, m.name):
            raise ValueError(f"unsafe tar member path: {m.name!r}")
        if m.issym() or m.islnk():
            link_target = (root / Path(m.name).parent / m.linkname)
            if m.issym() and _member_escapes(root, os.path.normpath(
                    str(Path(m.name).parent / m.linkname))):
                raise ValueError(f"escaping tar link rejected: {m.name!r} -> {m.linkname!r}")
            if m.islnk() and _member_escapes(root, m.linkname):
                raise ValueError(f"escaping tar hardlink rejected: {m.name!r} -> {m.linkname!r}")
        elif not (m.isreg() or m.isdir()):
            raise ValueError(f"non-regular tar member rejected: {m.name!r} (type {m.type!r})")
    tf.extractall(root, members=members or tf.getmembers())


def clone_repo(downloads_root: Path, row: dict[str, str], timeout_seconds: int) -> tuple[str, str, str, str]:
    source_type = row_source_type(row)
    if source_type == "archive":
        dest_name, status, detail = clone_archive_source(downloads_root, row, timeout_seconds)
    else:
        dest_name, status, detail = clone_git_source(downloads_root, row, timeout_seconds)
    return source_type, dest_name, status, detail


# --- Source provenance: resolved commit + license (issue 2, 2026-07-16) ------
# Publication used to be decided from synthesis/quality fields ONLY — a design
# with unknown redistribution terms and no reconstructible upstream revision
# could enter a release candidate. Record both AT THE SOURCE (the clone), so
# every downstream stage can carry them.

def resolved_commit(dest_dir: Path) -> str:
    """`git rev-parse HEAD` of the clone (works under --depth 1); '' for
    archives/non-git trees — the summary records exactly what is known."""
    try:
        r = subprocess.run(["git", "-C", str(dest_dir), "rev-parse", "HEAD"],
                           text=True, capture_output=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# Conservative SPDX-ish classifier: allow only well-known permissive terms;
# copyleft -> deny, weak-copyleft -> review, anything unclear -> unknown.
# Order matters: AFFERO/LESSER are matched before the bare GPL family.
_LICENSE_PATTERNS: list[tuple[str, str]] = [
    ("AGPL-3.0", "deny"), ("GNU AFFERO", "deny"),
    ("LGPL", "review"), ("GNU LESSER", "review"),
    ("GPL-3.0", "deny"), ("GPL-2.0", "deny"),
    ("GNU GENERAL PUBLIC LICENSE", "deny"),
    ("MPL-2.0", "review"), ("MOZILLA PUBLIC LICENSE", "review"),
    ("CERN-OHL", "review"), ("CERN OPEN HARDWARE", "review"),
    ("SOLDERPAD", "review"),
    ("APACHE-2.0", "allow"), ("APACHE LICENSE", "allow"),
    ("BSD-3-CLAUSE", "allow"), ("BSD-2-CLAUSE", "allow"),
    ("REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS", "allow"),  # BSD body
    ("MIT LICENSE", "allow"),
    ("PERMISSION IS HEREBY GRANTED, FREE OF CHARGE", "allow"),       # MIT body
    ("ISC LICENSE", "allow"), ("CC0-1.0", "allow"), ("UNLICENSE", "allow"),
]


def classify_license(repo_dir: Path) -> tuple[str, str]:
    """(license_status, evidence) from root license files. Statuses:
    allow | review | deny | unknown — the publish gate treats everything but
    'allow' as blocked (fail-closed on redistribution rights)."""
    candidates: list[Path] = []
    try:
        for p in sorted(repo_dir.iterdir()):
            if p.is_file() and re.match(r"(?i)^(LICEN[CS]E|COPYING)", p.name):
                candidates.append(p)
    except OSError:
        return "unknown", ""
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")[:20000].upper()
        except OSError:
            continue
        m = re.search(r"SPDX-LICENSE-IDENTIFIER:\s*([A-Z0-9.\-+]+)", text)
        if m:
            spdx = m.group(1)
            for pat, verdict in _LICENSE_PATTERNS:
                if pat in spdx:
                    return verdict, f"{p.name}:SPDX:{spdx}"
            return "review", f"{p.name}:SPDX:{spdx}"   # explicit but unrecognized
        for pat, verdict in _LICENSE_PATTERNS:
            if pat in text:
                return verdict, f"{p.name}:{pat}"
        return "unknown", f"{p.name}:unrecognized"
    return "unknown", "no_license_file"


def main() -> None:
    parser = argparse.ArgumentParser(description="Clone a CSV repo manifest into _downloads.")
    parser.add_argument("--repo-manifest-csv", type=Path, required=True)
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS)
    parser.add_argument("--summary-csv", type=Path, default=None)
    parser.add_argument("--clone-timeout-seconds", type=int, default=DEFAULT_CLONE_TIMEOUT_SECONDS)
    args = parser.parse_args()

    rows = load_manifest_rows(args.repo_manifest_csv)
    args.downloads_root.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, str]] = []
    cloned = 0
    skipped = 0
    failed = 0
    for row in rows:
        source_type, dest_name, status, detail = clone_repo(args.downloads_root, row, args.clone_timeout_seconds)
        commit = license_status = license_evidence = ""
        if status in ("cloned", "skipped_existing") and dest_name:
            dd = args.downloads_root / dest_name
            if dd.is_dir():
                commit = resolved_commit(dd)
                license_status, license_evidence = classify_license(dd)
        summary_rows.append(
            {
                "source_type": source_type,
                "source_url": row_source_url(row),
                "repo_url": row.get("repo_url", ""),
                "archive_url": row.get("archive_url", ""),
                "dest_name": dest_name,
                "branch": row.get("branch", ""),
                "status": status,
                "detail": detail,
                "notes": row.get("notes", ""),
                "resolved_commit": commit,
                "license_status": license_status or "unknown",
                "license_evidence": license_evidence,
            }
        )
        if status == "cloned":
            cloned += 1
        elif status == "skipped_existing":
            skipped += 1
        else:
            failed += 1

    summary_csv = args.summary_csv
    if summary_csv is None:
        summary_csv = args.repo_manifest_csv.with_name(args.repo_manifest_csv.stem + "_clone_summary.csv")
    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with summary_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["source_type", "source_url", "repo_url", "archive_url", "dest_name",
                        "branch", "status", "detail", "notes",
                        "resolved_commit", "license_status", "license_evidence"],
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"wrote {summary_csv}")
    print(f"manifest_rows {len(rows)}")
    print(f"cloned {cloned}")
    print(f"skipped_existing {skipped}")
    print(f"failed {failed}")


if __name__ == "__main__":
    main()
