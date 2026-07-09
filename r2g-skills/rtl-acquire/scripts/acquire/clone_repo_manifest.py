#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
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
                    zf.extractall(extract_root)
            elif tarfile.is_tarfile(archive_path):
                with tarfile.open(archive_path) as tf:
                    tf.extractall(extract_root)
            else:
                return dest_name, "failed", "unsupported archive format"
            flatten_extracted_tree(extract_root, dest_dir)
        except Exception as exc:
            if dest_dir.exists():
                shutil.rmtree(dest_dir, ignore_errors=True)
            return dest_name, "failed", f"archive extract failed: {exc}"
    return dest_name, "cloned", str(dest_dir)


def clone_repo(downloads_root: Path, row: dict[str, str], timeout_seconds: int) -> tuple[str, str, str, str]:
    source_type = row_source_type(row)
    if source_type == "archive":
        dest_name, status, detail = clone_archive_source(downloads_root, row, timeout_seconds)
    else:
        dest_name, status, detail = clone_git_source(downloads_root, row, timeout_seconds)
    return source_type, dest_name, status, detail


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
            fieldnames=["source_type", "source_url", "repo_url", "archive_url", "dest_name", "branch", "status", "detail", "notes"],
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
