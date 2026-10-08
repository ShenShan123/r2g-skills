#!/usr/bin/env python3
"""Safely acquire immutable repository revisions from the SQLite frontier."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.error
import urllib.request
import uuid
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from discovery_providers import GitHubProvider, HTTPJSONClient, ProviderError, _reset_at
from discovery_evidence import precision_policy_round
from frontier import FrontierDB, default_frontier_path, repository_revision_key, utc_now


ACQUISITION_SCHEMA = "rtl_acquisition_v1"
PARALLEL_ACQUISITION_SCHEMA = "bounded_parallel_acquisition_v2"
REPOSITORY_URL_RE = re.compile(r"(?:https?://(?:github\.com|gitlab\.com|codeberg\.org)/[^\s'\"<>]+|git@(?:github\.com|gitlab\.com|codeberg\.org):[^\s'\"<>]+)", re.I)
STRONG_RTL_RE = re.compile(r"(?:^|[-_\s])(rtl|hdl|verilog|systemverilog|vhdl|fpga|asic)(?:$|[-_\s])", re.I)


def acquisition_failure_category(detail: str) -> str:
    value = detail.upper()
    if "PROVIDER_RATE_LIMIT" in value or "RATE_LIMITED" in value:
        return "PROVIDER_RATE_LIMIT"
    if "REVISION_RESOLUTION_FAILED" in value:
        return "REVISION_RESOLUTION"
    if "ARCHIVE_TOO_LARGE" in value:
        return "ARCHIVE_TOO_LARGE"
    if "EXTRACT_LIMIT_EXCEEDED" in value:
        return "EXTRACTION_LIMIT"
    if "HTTP ERROR 404" in value:
        return "HTTP_NOT_FOUND"
    if "HTTP" in value or "URL" in value or "TIMEOUT" in value:
        return "HTTP_TRANSIENT"
    return "OTHER"


def strong_rtl_confidence(row: dict[str, Any]) -> bool:
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    evidence = metadata.get("discovery_evidence", {})
    anchors = set(evidence.get("rtl_anchors", [])) if isinstance(evidence, dict) else set()
    direct_anchor = bool(anchors & {"DIRECT_HDL_LANGUAGE", "DIRECT_HDL_FILE", "HDL_MANIFEST"})
    # The bounded large-repository lane requires direct RTL/ecosystem evidence;
    # graph proximity and a keyword-like repository name are not sufficient.
    return float(row.get("design_likelihood") or 0.0) >= 0.8 and direct_anchor


def category_failure_count(db: FrontierDB, repository_key: str, category: str, since: str) -> int:
    rows = db.connection.execute(
        """SELECT COALESCE(error_detail,'') FROM acquisition_attempts
           WHERE repository_key=? AND state='FAILED' AND started_at>=?""",
        (repository_key, since),
    ).fetchall()
    return sum(acquisition_failure_category(str(row[0])) == category for row in rows)


def retry_allowed(category: str, failures_including_current: int, high_rtl_confidence: bool) -> bool:
    limits = {
        "REVISION_RESOLUTION": 2,
        "ARCHIVE_TOO_LARGE": 2 if high_rtl_confidence else 1,
        "EXTRACTION_LIMIT": 2 if high_rtl_confidence else 1,
        "HTTP_NOT_FOUND": 1,
        "HTTP_TRANSIENT": 3,
        "OTHER": 2,
    }
    if category == "PROVIDER_RATE_LIMIT":
        return True
    return failures_including_current < limits[category]


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def safe_relative(name: str) -> Path | None:
    pure = PurePosixPath(name)
    parts = list(pure.parts)
    if not parts or pure.is_absolute() or ".." in parts:
        return None
    if len(parts) > 1:
        parts = parts[1:]
    return Path(*parts) if parts else None


def stream_download(
    url: str, destination: Path, headers: dict[str, str], max_bytes: int,
    provider: str,
) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "rtl-expander/1", **headers})
    total = 0
    sha = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("ARCHIVE_TOO_LARGE")
                sha.update(chunk)
                output.write(chunk)
    except urllib.error.HTTPError as exc:
        response_headers = {key.lower(): value for key, value in exc.headers.items()}
        remaining = response_headers.get("x-ratelimit-remaining") or response_headers.get("ratelimit-remaining")
        rate_limited = exc.code == 429 or (
            exc.code == 403 and (remaining == "0" or bool(response_headers.get("retry-after")))
        )
        if rate_limited:
            retry_after, reset_at = _reset_at(response_headers, response_headers.get("retry-after"))
            raise ProviderError(
                f"PROVIDER_RATE_LIMIT:{provider}:HTTP_{exc.code}",
                retry_after=max(60, retry_after), reset_at=reset_at,
                status_code=exc.code, rate_limited=True,
            ) from exc
        raise
    return total, sha.hexdigest()


def safe_extract_archive(archive: Path, destination: Path, max_files: int, max_bytes: int) -> tuple[int, int]:
    destination.mkdir(parents=True, exist_ok=True)
    files = total = 0
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as handle:
            for member in handle:
                relative = safe_relative(member.name)
                if relative is None or member.isdir():
                    continue
                if member.issym() or member.islnk() or not member.isfile():
                    continue
                files += 1
                total += max(0, member.size)
                if files > max_files or total > max_bytes:
                    raise RuntimeError("EXTRACT_LIMIT_EXCEEDED")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source = handle.extractfile(member)
                if source is None:
                    continue
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    elif zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            for member in handle.infolist():
                relative = safe_relative(member.filename)
                if relative is None or member.is_dir():
                    continue
                mode = member.external_attr >> 16
                if mode & 0o170000 == 0o120000:
                    continue
                files += 1
                total += member.file_size
                if files > max_files or total > max_bytes:
                    raise RuntimeError("EXTRACT_LIMIT_EXCEEDED")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    else:
        raise RuntimeError("UNSUPPORTED_ARCHIVE")
    return files, total


def discover_static_repository_edges(db: FrontierDB, repository_key: str, source_root: Path) -> int:
    count = 0
    candidates = list(source_root.glob(".gitmodules"))
    candidates.extend(source_root.glob("**/Bender.yml"))
    candidates.extend(source_root.glob("**/*.core"))
    candidates.extend(path for path in source_root.glob("README*") if path.is_file())
    seen: set[tuple[str, str]] = set()
    for path in candidates[:500]:
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        edge_type = "submodule" if path.name == ".gitmodules" else "dependency" if path.name in {"Bender.yml"} or path.suffix == ".core" else "readme_reference"
        for match in REPOSITORY_URL_RE.findall(text):
            url = match.rstrip("/).,;]")
            signature = (edge_type, url.lower())
            if signature in seen:
                continue
            seen.add(signature)
            try:
                db.add_edge(repository_key, url, edge_type, str(path.relative_to(source_root)))
                count += 1
            except ValueError:
                continue
    return count


def provider_revision_and_archive(row: dict[str, Any], client: HTTPJSONClient) -> tuple[str, str, dict[str, str]]:
    provider, namespace, name = row["provider"], row["namespace"], row["repo_name"]
    branch = row.get("default_branch") or "HEAD"
    refs = ["HEAD"] if branch == "HEAD" else [f"refs/heads/{branch}"]
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", row["canonical_url"] + ".git", *refs],
            text=True, capture_output=True, timeout=60, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"REVISION_RESOLUTION_FAILED:{type(exc).__name__}") from exc
    match = re.search(r"^([0-9a-fA-F]{40,64})\s", result.stdout, re.M)
    if result.returncode != 0 or not match:
        diagnostic = (result.stderr + "\n" + result.stdout).lower()
        if any(token in diagnostic for token in ("429", "rate limit", "too many requests", "retry-after")):
            raise ProviderError(
                f"PROVIDER_RATE_LIMIT:{provider}:REVISION_RESOLUTION",
                retry_after=300, rate_limited=True,
            )
        raise RuntimeError(f"REVISION_RESOLUTION_FAILED:git_exit_{result.returncode}")
    sha = match.group(1).lower()
    if provider == "github":
        github = GitHubProvider(client)
        return sha, f"https://codeload.github.com/{namespace}/{name}/tar.gz/{sha}", github._headers()
    if provider == "gitlab":
        project = urllib.parse.quote(f"{namespace}/{name}", safe="")
        token = os.environ.get("GITLAB_TOKEN")
        headers = {"PRIVATE-TOKEN": token} if token else {}
        return sha, f"https://gitlab.com/api/v4/projects/{project}/repository/archive.tar.gz?sha={sha}", headers
    if provider == "codeberg":
        return sha, f"https://codeberg.org/{namespace}/{name}/archive/{sha}.tar.gz", {}
    raise RuntimeError(f"UNSUPPORTED_PROVIDER:{provider}")


def revision_destination(corpus: Path, row: dict[str, Any], commit_sha: str) -> Path:
    return corpus / "repositories" / row["provider"] / Path(*row["namespace"].split("/")) / row["repo_name"] / commit_sha


def acquire_archive(db: FrontierDB, corpus: Path, row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    high_confidence = strong_rtl_confidence(row)
    prior_oversize = category_failure_count(
        db, row["repository_key"], "ARCHIVE_TOO_LARGE", getattr(args, "round_started_at", "1970-01-01T00:00:00+00:00")
    )
    lane = "standard"
    effective_args = args
    if getattr(args, "enable_large_repository_lane", False) and high_confidence and prior_oversize:
        lane = "large_repository"
        effective_args = copy.copy(args)
        effective_args.max_archive_bytes = getattr(args, "large_lane_max_archive_bytes", 1024 * 1024 * 1024)
        effective_args.max_extract_bytes = getattr(args, "large_lane_max_extract_bytes", 4 * 1024 * 1024 * 1024)
        effective_args.max_files = getattr(args, "large_lane_max_files", 400_000)
    selection_lane = str(row.get("selection_lane") or "precision")
    attempt_method = "archive_large" if lane != "standard" else "archive"
    if selection_lane == "exploration":
        attempt_method += "_exploration"
    attempt = db.start_attempt(row["repository_key"], attempt_method)
    client = HTTPJSONClient(timeout=args.network_timeout)
    try:
        sha, url, headers = provider_revision_and_archive(row, client)
        destination = revision_destination(corpus, row, sha)
        if (destination / "repository.json").is_file():
            db.record_provider_success(row["provider"], source="acquisition_canary")
            db.finish_acquisition(attempt, row["repository_key"], "ACQUIRED", commit_sha=sha, source_path=str(destination / "source"))
            edges = discover_static_repository_edges(db, row["repository_key"], destination / "source")
            return {"repository_key": row["repository_key"], "state": "CACHE_HIT", "revision": sha, "edges": edges}
        staging_root = corpus / "repositories" / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rtl-acquire-", dir=staging_root) as temporary:
            stage = Path(temporary)
            archive = stage / "archive"
            downloaded, archive_hash = stream_download(
                url, archive, headers, effective_args.max_archive_bytes, row["provider"]
            )
            files, extracted = safe_extract_archive(archive, stage / "source", effective_args.max_files, effective_args.max_extract_bytes)
            atomic_json(stage / "repository.json", {
                "schema": ACQUISITION_SCHEMA, "repository_key": row["repository_key"],
                "repository_revision_key": repository_revision_key(row["repository_key"], sha),
                "provider": row["provider"], "canonical_url": row["canonical_url"], "commit_sha": sha,
                "archive_url": url, "archive_sha256": archive_hash, "files": files,
                "extracted_bytes": extracted, "acquired_at": utc_now(), "method": "archive",
            })
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, destination)
        db.record_provider_success(row["provider"], source="acquisition_canary")
        db.finish_acquisition(
            attempt, row["repository_key"], "ACQUIRED", commit_sha=sha,
            source_path=str(destination / "source"), archive_sha256=archive_hash,
            bytes_downloaded=downloaded, files_extracted=files,
        )
        edges = discover_static_repository_edges(db, row["repository_key"], destination / "source")
        return {"repository_key": row["repository_key"], "state": "ACQUIRED", "revision": sha, "files": files, "edges": edges, "lane": lane}
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, ProviderError) and exc.rate_limited:
            cooldown = db.set_provider_rate_limit(
                row["provider"], retry_after=exc.retry_after, reset_at=exc.reset_at,
                source="acquisition", detail=detail,
            )
            db.finish_acquisition(
                attempt, row["repository_key"], "RATE_LIMITED",
                error_class="PROVIDER_RATE_LIMIT", error_detail=detail,
                retry=True, retry_at=cooldown["reset_at"],
            )
            return {
                "repository_key": row["repository_key"], "state": "RATE_LIMITED",
                "provider": row["provider"], "retry_after": cooldown["retry_after_seconds"],
                "reset_at": cooldown["reset_at"], "retry_count_not_consumed": True,
                "lane": lane,
            }
        # A canary that reaches a non-quota response has proven provider
        # availability even if this particular candidate is invalid.
        db.record_provider_success(row["provider"], source="acquisition_canary_non_quota_response")
        category = acquisition_failure_category(detail)
        failures = category_failure_count(
            db, row["repository_key"], category,
            getattr(args, "round_started_at", "1970-01-01T00:00:00+00:00"),
        ) + 1
        retry = retry_allowed(category, failures, high_confidence)
        db.finish_acquisition(
            attempt, row["repository_key"], "FAILED", error_class=type(exc).__name__,
            error_detail=detail, retry=retry,
        )
        return {
            "repository_key": row["repository_key"], "state": "FAILED",
            "error": f"{type(exc).__name__}:{exc}", "failure_category": category,
            "retry_allowed": retry, "lane": lane,
        }


def tracked_files(repo: Path) -> Iterable[Path]:
    try:
        result = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [repo / os.fsdecode(value) for value in result.stdout.split(b"\0") if value]


def ingest_local_repository(db: FrontierDB, corpus: Path, repo: Path, args: argparse.Namespace) -> dict[str, Any]:
    attempt: str | None = None
    repository_key: str | None = None
    try:
        remote = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=repo, text=True, capture_output=True, timeout=5, check=False).stdout.strip()
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, timeout=5, check=False).stdout.strip().lower()
        if not remote or not commit:
            return {"repository": repo.name, "state": "SKIPPED_NO_IMMUTABLE_ID"}
        repository_key, _ = db.upsert_repository({"url": remote, "evidence": str(repo)}, "existing_local")
        row = dict(db.connection.execute("SELECT * FROM repositories WHERE repository_key=?", (repository_key,)).fetchone())
        destination = revision_destination(corpus, row, commit)
        attempt = db.start_attempt(repository_key, "local_tracked_snapshot")
        if (destination / "repository.json").is_file():
            db.finish_acquisition(attempt, repository_key, "ACQUIRED", commit_sha=commit, source_path=str(destination / "source"))
            edges = discover_static_repository_edges(db, repository_key, destination / "source")
            return {"repository": repo.name, "state": "CACHE_HIT", "revision": commit, "edges": edges}
        staging_root = corpus / "repositories" / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rtl-local-", dir=staging_root) as temporary:
            stage = Path(temporary)
            files = total = 0
            for source in tracked_files(repo):
                if not source.is_file() or source.is_symlink():
                    continue
                relative = source.relative_to(repo)
                size = source.stat().st_size
                files += 1
                total += size
                if files > args.max_files or total > args.max_extract_bytes:
                    raise RuntimeError("LOCAL_SNAPSHOT_LIMIT_EXCEEDED")
                target = stage / "source" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            atomic_json(stage / "repository.json", {
                "schema": ACQUISITION_SCHEMA, "repository_key": repository_key,
                "repository_revision_key": repository_revision_key(repository_key, commit),
                "provider": row["provider"], "canonical_url": row["canonical_url"], "commit_sha": commit,
                "files": files, "extracted_bytes": total, "acquired_at": utc_now(),
                "method": "local_tracked_snapshot", "source_local_path": str(repo),
            })
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, destination)
        db.finish_acquisition(attempt, repository_key, "ACQUIRED", commit_sha=commit, source_path=str(destination / "source"), files_extracted=files)
        edges = discover_static_repository_edges(db, repository_key, destination / "source")
        return {"repository": repo.name, "state": "ACQUIRED", "revision": commit, "files": files, "bytes": total, "edges": edges}
    except Exception as exc:
        if attempt and repository_key:
            db.finish_acquisition(attempt, repository_key, "FAILED", error_class=type(exc).__name__, error_detail=str(exc))
        return {"repository": repo.name, "state": "FAILED", "error": f"{type(exc).__name__}:{exc}"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work" / "data" / "rtl_corpus")
    parser.add_argument("--max-repos", type=int, default=500)
    parser.add_argument("--worker-id", default=f"worker-{uuid.uuid4().hex[:12]}")
    parser.add_argument("--max-archive-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--max-extract-bytes", type=int, default=2 * 1024 * 1024 * 1024)
    parser.add_argument("--max-files", type=int, default=200_000)
    parser.add_argument("--network-timeout", type=int, default=30)
    parser.add_argument("--max-repo-seconds", type=int, default=300)
    parser.add_argument("--min-priority", type=float, default=1.0)
    parser.add_argument("--min-design-likelihood", type=float, default=0.5)
    parser.add_argument("--exploration-fraction", type=float, default=0.15,
                        help="Bounded share of evidence-rich candidates below the normal likelihood gate")
    parser.add_argument("--providers", default="", help="Optional comma-separated provider allowlist")
    parser.add_argument("--ingest-local-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--controller-round-id",
        default="",
        help="Internal: acquisition is owned by the named target controller",
    )
    parser.add_argument("--round-started-at", default="1970-01-01T00:00:00+00:00")
    parser.add_argument("--round-revision-target", type=int, default=0)
    parser.add_argument("--enable-large-repository-lane", action="store_true")
    parser.add_argument("--large-lane-max-archive-bytes", type=int, default=1024 * 1024 * 1024)
    parser.add_argument("--large-lane-max-extract-bytes", type=int, default=4 * 1024 * 1024 * 1024)
    parser.add_argument("--large-lane-max-files", type=int, default=400_000)
    parser.add_argument("--bounded-parallel-acquisition", action="store_true")
    parser.add_argument("--parallel-fast-workers", type=int, default=3)
    parser.add_argument("--parallel-unknown-workers", type=int, default=1)
    parser.add_argument("--parallel-slow-workers", type=int, default=1)
    parser.add_argument("--parallel-slow-fraction", type=float, default=0.20)
    parser.add_argument("--parallel-size-threshold-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--candidate-size-lane", choices=("any", "fast", "unknown", "slow"), default="any")
    parser.add_argument("--acquisition-executor-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--internal-sequential-worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def persistent_worker_specs(
    fast_workers: int, unknown_workers: int, slow_workers: int,
) -> list[tuple[str, int]]:
    """Return persistent workers; capacity belongs to the shared executor."""
    if min(fast_workers, unknown_workers, slow_workers) < 0:
        raise ValueError("parallel worker counts must be non-negative")
    if fast_workers + unknown_workers + slow_workers <= 0:
        raise ValueError("at least one parallel acquisition worker is required")
    specs: list[tuple[str, int]] = []
    # Start reserved slow/unknown capacity before fast workers can steal.
    for lane, workers in (
        ("slow", slow_workers), ("unknown", unknown_workers), ("fast", fast_workers),
    ):
        specs.extend((lane, index) for index in range(workers))
    return specs


def lane_steal_order(home_lane: str) -> tuple[str, ...]:
    return {
        "fast": ("fast", "unknown", "slow"),
        "unknown": ("unknown", "fast", "slow"),
        "slow": ("slow", "fast", "unknown"),
        "any": ("any",),
    }[home_lane]


def partition_worker_budgets(
    total: int, fast_workers: int, slow_workers: int, slow_fraction: float,
) -> list[tuple[str, int, int]]:
    """Compatibility partition used until the v2 executor migration commits."""
    if total <= 0:
        return []
    if fast_workers < 0 or slow_workers < 0 or fast_workers + slow_workers <= 0:
        raise ValueError("parallel worker counts must be non-negative with at least one worker")
    fraction = max(0.0, min(0.5, slow_fraction))
    slow_total = min(total, int(round(total * fraction))) if slow_workers else 0
    fast_total = total - slow_total
    if not fast_workers:
        slow_total, fast_total = total, 0
    partitions: list[tuple[str, int, int]] = []
    for lane, workers, lane_total in (
        ("fast", fast_workers, fast_total), ("slow", slow_workers, slow_total),
    ):
        if workers <= 0:
            continue
        quotient, remainder = divmod(lane_total, workers)
        for index in range(workers):
            budget = quotient + (1 if index < remainder else 0)
            if budget:
                partitions.append((lane, index, budget))
    return partitions


def _without_option(argv: list[str], option: str) -> list[str]:
    cleaned: list[str] = []
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == option:
            index += 2
            continue
        if value.startswith(option + "="):
            index += 1
            continue
        cleaned.append(value)
        index += 1
    return cleaned


def prepare_acquisition_frontier(args: argparse.Namespace) -> None:
    with FrontierDB(default_frontier_path(args.corpus_root)) as db:
        precision_policy = (
            db.discovery_precision_policy()
            if precision_policy_round(args.controller_round_id) else None
        )
        db.quarantine_malformed_repositories()
        db.reprioritize_from_edges()
        db.reprioritize_hardware_likelihood(precision_policy)
        db.requeue_stale_claims()
        db.reconcile_abandoned_attempts()
        if args.controller_round_id:
            db.initialize_round_acquisition_budget(
                args.controller_round_id, args.round_revision_target,
                args.exploration_fraction, args.round_started_at,
            )


def run_bounded_parallel(args: argparse.Namespace) -> int:
    """Run persistent work-stealing lanes under one shared attempt cap."""
    if args.ingest_local_root or args.dry_run:
        raise ValueError("bounded parallel acquisition supports live frontier archives only")
    workers = persistent_worker_specs(
        args.parallel_fast_workers, args.parallel_unknown_workers,
        args.parallel_slow_workers,
    )
    prepare_acquisition_frontier(args)
    executor_id = "acqexec_" + uuid.uuid4().hex
    with FrontierDB(default_frontier_path(args.corpus_root)) as db:
        db.initialize_acquisition_executor(
            executor_id, args.controller_round_id, args.max_repos,
        )
    base = list(sys.argv[1:])
    for option in (
        "--max-repos", "--worker-id", "--candidate-size-lane",
        "--acquisition-executor-id",
    ):
        base = _without_option(base, option)
    base = [value for value in base if value not in {"--bounded-parallel-acquisition", "--internal-sequential-worker"}]
    launch = {
        "schema": PARALLEL_ACQUISITION_SCHEMA,
        "state": "STARTING",
        "executor_id": executor_id,
        "max_repos": args.max_repos,
        "size_threshold_bytes": args.parallel_size_threshold_bytes,
        "workers": [
            {"lane": lane, "index": index} for lane, index in workers
        ],
    }
    print(json.dumps(launch, sort_keys=True), flush=True)
    children: list[tuple[str, int, str, subprocess.Popen[Any]]] = []
    for lane, index in workers:
        worker_id = f"{args.worker_id}-{lane}-{index}"
        command = [
            sys.executable, str(Path(__file__)), *base,
            "--internal-sequential-worker",
            "--candidate-size-lane", lane,
            "--parallel-size-threshold-bytes", str(args.parallel_size_threshold_bytes),
            "--max-repos", str(args.max_repos),
            "--acquisition-executor-id", executor_id,
            "--worker-id", worker_id,
        ]
        children.append((lane, index, worker_id, subprocess.Popen(
            command, pass_fds=tuple(getattr(args, "coordination_fds", ())),
        )))
    worker_results = []
    aggregate_states: Counter[str] = Counter()
    aggregate_anchors: defaultdict[str, Counter[str]] = defaultdict(Counter)
    total_attempted = 0
    worker_summary_root = args.corpus_root / "state" / "acquisition_workers"
    for lane, index, worker_id, child in children:
        returncode = child.wait()
        result: dict[str, Any] = {
            "lane": lane, "index": index,
            "worker_id": worker_id, "pid": child.pid, "returncode": returncode,
        }
        worker_summary_path = worker_summary_root / f"{worker_id}.json"
        if worker_summary_path.is_file():
            worker_summary = json.loads(worker_summary_path.read_text(encoding="utf-8"))
            result["attempted"] = int(worker_summary.get("attempted", 0))
            result["states"] = worker_summary.get("states", {})
            total_attempted += result["attempted"]
            aggregate_states.update(result["states"])
            for anchor, counts in worker_summary.get("admission_anchor", {}).items():
                aggregate_anchors[anchor].update(counts)
        worker_results.append(result)
    failures = [result for result in worker_results if result["returncode"] != 0]
    with FrontierDB(default_frontier_path(args.corpus_root)) as db:
        db.finish_acquisition_executor(executor_id, "FAILED" if failures else "COMPLETE")
        summary = {
            "schema": PARALLEL_ACQUISITION_SCHEMA,
            "state": "FAILED" if failures else "PASS",
            "timestamp": utc_now(),
            "executor_id": executor_id,
            "max_repos": args.max_repos,
            "attempted": total_attempted,
            "states": dict(aggregate_states),
            "admission_anchor": {
                anchor: dict(counts) for anchor, counts in sorted(aggregate_anchors.items())
            },
            "size_threshold_bytes": args.parallel_size_threshold_bytes,
            "worker_results": worker_results,
            "worker_failures": failures,
            "frontier": db.counts(),
            "provider_status": db.provider_statuses(
                [value.strip() for value in args.providers.split(",") if value.strip()]
            ),
            "round_acquisition_budget": db.round_acquisition_budget_status(
                args.controller_round_id
            ),
        }
    atomic_json(args.corpus_root / "snapshots" / "acquisition_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if failures else 0


def run(args: argparse.Namespace) -> int:
    selected_providers = [value.strip() for value in args.providers.split(",") if value.strip()]
    results: list[dict[str, Any]] = []
    with FrontierDB(default_frontier_path(args.corpus_root)) as db:
        precision_policy = (
            db.discovery_precision_policy()
            if precision_policy_round(args.controller_round_id) else None
        )
        if not args.internal_sequential_worker:
            db.quarantine_malformed_repositories()
            db.reprioritize_from_edges()
            db.reprioritize_hardware_likelihood(precision_policy)
            db.requeue_stale_claims()
            db.reconcile_abandoned_attempts()
            if args.controller_round_id:
                db.initialize_round_acquisition_budget(
                    args.controller_round_id, args.round_revision_target,
                    args.exploration_fraction, args.round_started_at,
                )
        if args.ingest_local_root:
            repos = sorted((path for path in args.ingest_local_root.iterdir() if path.is_dir()), key=lambda path: path.name.lower())[:args.max_repos]
            if args.dry_run:
                print(json.dumps({"mode": "local_snapshot", "candidate_count": len(repos), "candidates": [path.name for path in repos]}, indent=2))
                return 0
            for repo in repos:
                print(f"[snapshot] {repo.name}", flush=True)
                results.append(ingest_local_repository(db, args.corpus_root, repo, args))
        else:
            if args.dry_run:
                provider_clause = f" AND provider IN ({','.join('?' for _ in selected_providers)})" if selected_providers else ""
                rows = db.connection.execute(f"SELECT repository_key,priority,design_likelihood FROM repositories WHERE state='FRONTIER' AND acquisition_status IN ('NOT_ACQUIRED','RETRY') AND priority>=? AND design_likelihood>=? {provider_clause} ORDER BY priority DESC LIMIT ?", (args.min_priority, args.min_design_likelihood, *selected_providers, args.max_repos)).fetchall()
                print(json.dumps({"mode": "archive", "candidate_count": len(rows), "candidates": [dict(row) for row in rows]}, indent=2))
                return 0
            def acquisition_timeout(_signum: int, _frame: Any) -> None:
                raise TimeoutError("ACQUISITION_WALL_CLOCK_EXCEEDED")

            signal.signal(signal.SIGALRM, acquisition_timeout)
            for _claim_index in range(args.max_repos):
                row = None
                # Prefer production in every size lane. Exploration is tried
                # only after production is empty and its round-wide cap permits
                # another active claim.
                for exploration in (False, True):
                    for size_lane in lane_steal_order(args.candidate_size_lane):
                        row = db.claim_repository(
                            args.worker_id, args.min_priority, args.min_design_likelihood,
                            selected_providers, exploration=exploration,
                            precision_policy=bool(precision_policy),
                            size_lane=size_lane,
                            size_threshold_bytes=args.parallel_size_threshold_bytes,
                            round_id=args.controller_round_id,
                            executor_id=args.acquisition_executor_id,
                        )
                        if row is not None:
                            break
                    if row is not None:
                        break
                if row is None:
                    break
                lane = str(row.get("selection_lane") or "production")
                metadata = json.loads(row.get("metadata_json") or "{}")
                admission_anchor = str(
                    (metadata.get("discovery_evidence") or {}).get("admission_anchor") or "UNANCHORED"
                )
                print(f"[acquire] {row['repository_key']}", flush=True)
                signal.alarm(max(1, args.max_repo_seconds))
                try:
                    result = acquire_archive(db, args.corpus_root, row, args)
                    result["admission_anchor"] = admission_anchor
                    results.append(result)
                except KeyboardInterrupt:
                    db.release_claim(row["repository_key"], args.worker_id)
                    raise
                finally:
                    signal.alarm(0)
        summary = {
            "schema": "rtl_acquisition_summary_v1", "timestamp": utc_now(),
            "attempted": len(results), "states": {}, "frontier": db.counts(),
            "provider_status": db.provider_statuses(selected_providers),
            "exploration_fraction_cap": min(0.2, max(0.0, args.exploration_fraction)),
        }
        if args.controller_round_id:
            summary["round_acquisition_budget"] = db.round_acquisition_budget_status(
                args.controller_round_id
            )
        for result in results:
            summary["states"][result["state"]] = summary["states"].get(result["state"], 0) + 1
        anchor_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
        for result in results:
            anchor = str(result.get("admission_anchor") or "UNANCHORED")
            anchor_counts[anchor]["attempted"] += 1
            anchor_counts[anchor][f"state:{result['state']}"] += 1
        summary["admission_anchor"] = {
            anchor: dict(counts) for anchor, counts in sorted(anchor_counts.items())
        }
        output = args.corpus_root / "snapshots" / "acquisition_summary.json"
        if args.internal_sequential_worker:
            summary["candidate_size_lane"] = args.candidate_size_lane
            atomic_json(
                args.corpus_root / "state" / "acquisition_workers" / f"{args.worker_id}.json",
                summary,
            )
        else:
            atomic_json(output, summary)
        if args.internal_sequential_worker:
            print(json.dumps({
                "schema": "bounded_parallel_acquisition_worker_v2",
                "worker_id": args.worker_id,
                "lane": args.candidate_size_lane,
                "attempted": summary["attempted"],
                "states": summary["states"],
            }, sort_keys=True), flush=True)
        else:
            print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main() -> int:
    args = parse_args()
    state_dir = args.corpus_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    owner_path = state_dir / "acquisition_target_owner.lock"
    freeze_path = state_dir / "acquisition_freeze.lock"
    with owner_path.open("a+") as owner_handle, freeze_path.open("a+") as freeze_handle:
        if not args.controller_round_id:
            try:
                # An ordinary worker may coexist with other ordinary workers, but
                # not with a target controller that must freeze an exact cohort.
                fcntl.flock(owner_handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({
                    "schema": "rtl_acquisition_coordination_v1",
                    "state": "RESERVED_BY_TARGET_CONTROLLER",
                }, indent=2, sort_keys=True))
                return 3
        # Cohort locking takes the exclusive side of this lock.  Consequently a
        # lock snapshot cannot race a worker that is still acquiring a revision.
        fcntl.flock(freeze_handle, fcntl.LOCK_SH)
        # Parallel children inherit both locked descriptors. If their parent is
        # interrupted, the controller ownership and cohort barrier remain held
        # until every already-issued acquisition worker exits.
        args.coordination_fds = (owner_handle.fileno(), freeze_handle.fileno())
        if args.bounded_parallel_acquisition and not args.internal_sequential_worker:
            return run_bounded_parallel(args)
        return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
