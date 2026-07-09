#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from skill_env import default_downloads_root, default_out_root, default_python_bin, default_workspace_root
PYTHON_BIN = default_python_bin()
DISCOVER_REPOS = SCRIPT_DIR / "acquire" / "discover_repo_manifest_candidates.py"
RUN_ROUND = SCRIPT_DIR / "run_expansion_round.py"
REFRESH_SCAN_STATE = SCRIPT_DIR / "acquire" / "refresh_downloads_scan_state.py"
SCORE_REPOS = SCRIPT_DIR / "report" / "score_download_repos.py"
DEFAULT_DOWNLOADS = default_downloads_root()
DEFAULT_WORK_ROOT = default_workspace_root()
DEFAULT_SCAN_STATE = DEFAULT_WORK_ROOT / "scan_state" / "downloads_scan_state.json"
DEFAULT_INDEX = default_out_root() / "index.csv"
DEFAULT_AUDIT_CSV = DEFAULT_DOWNLOADS / "downloads_expansion_audit.csv"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.open(newline="", encoding="utf-8")))


def load_scan_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    repos = payload.get("repos", {})
    return repos if isinstance(repos, dict) else {}


def load_recent_clone_timeouts(work_root: Path) -> set[str]:
    timed_out: set[str] = set()
    for path in sorted(work_root.glob("*_clone_summary.csv")):
        for row in load_csv(path):
            if row.get("status") != "failed":
                continue
            detail = (row.get("detail") or "").lower()
            if "timed out" not in detail and "timeout" not in detail:
                continue
            dest_name = (row.get("dest_name") or "").strip()
            if dest_name:
                timed_out.add(dest_name)
    return timed_out


def current_targets(index_csv: Path) -> tuple[int, int]:
    success = 0
    medium_large = 0
    for row in load_csv(index_csv):
        if row.get("status") != "success":
            continue
        success += 1
        try:
            cells = int(row.get("cells", "") or 0)
        except Exception:
            cells = 0
        if cells >= 1000:
            medium_large += 1
    return success, medium_large


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def choose_manifest_rows(
    candidates_csv: Path,
    scan_state_json: Path,
    *,
    repo_batch_size: int,
    allowed_buckets: set[str],
    include_already_cloned: bool,
) -> list[dict[str, str]]:
    rows = load_csv(candidates_csv)
    scan_state = load_scan_state(scan_state_json)
    clone_timeouts = load_recent_clone_timeouts(candidates_csv.parent)
    bucket_rank = {"recommended_now": 0, "review": 1, "conditional": 2}
    size_rank = {"large": 0, "medium": 1, "unknown": 2}
    rows.sort(
        key=lambda row: (
            bucket_rank.get(row.get("quality_bucket", ""), 9),
            size_rank.get(row.get("size_guess", ""), 9),
            -(int(row.get("score", "0") or 0)),
            -(int(row.get("stars", "0") or 0)),
            row.get("full_name", ""),
        )
    )
    chosen: list[dict[str, str]] = []
    seen_dest: set[str] = set()
    for row in rows:
        dest_name = row.get("dest_name", "")
        if not dest_name or dest_name in seen_dest:
            continue
        if dest_name in clone_timeouts:
            continue
        if row.get("quality_bucket") not in allowed_buckets:
            continue
        repo_state = scan_state.get(dest_name, {})
        if repo_state.get("repo_decision") == "reject":
            continue
        already_cloned = row.get("already_cloned", "").lower() == "true"
        if already_cloned:
            state_status = str(repo_state.get("status", ""))
            audit = repo_state.get("audit", {})
            not_run_count = int(audit.get("not_run_count", 0) or 0)
            if not include_already_cloned and state_status == "scanned" and not_run_count == 0:
                continue
            if not include_already_cloned and state_status != "scanned":
                continue
        chosen.append(
            {
                "source_type": row.get("source_type", "git"),
                "source_url": row.get("source_url") or row.get("repo_url", ""),
                "archive_url": row.get("archive_url", ""),
                "repo_url": row.get("repo_url", ""),
                "dest_name": dest_name,
                "branch": row.get("branch", ""),
                "depth": row.get("depth", "1"),
                "notes": row.get("notes", ""),
            }
        )
        seen_dest.add(dest_name)
        if len(chosen) >= repo_batch_size:
            break
    return chosen


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["source_type", "source_url", "archive_url", "repo_url", "dest_name", "branch", "depth", "notes"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_round_summary(
    path: Path,
    *,
    round_idx: int,
    keywords: list[str],
    approved_manifest: Path | None,
    chosen_rows: list[dict[str, str]],
    success_before: int,
    medium_large_before: int,
    success_after: int,
    medium_large_after: int,
    status: str,
    note: str,
) -> None:
    payload = {
        "generated_at": now_iso(),
        "round": round_idx,
        "keywords": keywords,
        "approved_manifest_csv": str(approved_manifest) if approved_manifest else "",
        "selected_repo_count": len(chosen_rows),
        "selected_repos": [row.get("dest_name", "") for row in chosen_rows],
        "external_success_before": success_before,
        "medium_large_before": medium_large_before,
        "external_success_after": success_after,
        "medium_large_after": medium_large_after,
        "external_success_delta": success_after - success_before,
        "medium_large_delta": medium_large_after - medium_large_before,
        "status": status,
        "note": note,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Search GitHub, clone curated repo batches, expand the dataset, score repos, and repeat until target counts are met.")
    parser.add_argument("--keyword", action="append", required=True)
    parser.add_argument("--backend", action="append", choices=["github", "gitlab", "gitee"], default=[])
    parser.add_argument("--min-stars", type=int, default=5)
    parser.add_argument("--preferred-size", choices=["medium", "large"], action="append", default=[])
    parser.add_argument("--quality-profile", choices=["pure_rtl", "broad"], default="pure_rtl")
    parser.add_argument("--target-external-success", type=int, default=520)
    parser.add_argument("--target-medium-large-success", type=int, default=250)
    parser.add_argument("--repo-batch-size", type=int, default=5)
    parser.add_argument("--max-rounds", type=int, default=10)
    parser.add_argument("--max-stalled-rounds", type=int, default=2)
    parser.add_argument("--include-already-cloned", action="store_true")
    parser.add_argument("--allow-conditional-only-rounds", action="store_true")
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS)
    parser.add_argument("--scan-state-json", type=Path, default=DEFAULT_SCAN_STATE)
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--audit-csv", type=Path, default=DEFAULT_AUDIT_CSV)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--min-repo-success", type=int, default=2)
    parser.add_argument("--min-medium-large-success", type=int, default=1)
    parser.add_argument("--min-failures-before-reject", type=int, default=3)
    parser.add_argument("--max-fail-ratio", type=float, default=0.85)
    parser.add_argument("--reject-if-all-small", action="store_true")
    args = parser.parse_args()

    allowed_buckets = {"recommended_now", "review"}
    if args.allow_conditional_only_rounds:
        allowed_buckets.add("conditional")
    stalled_rounds = 0
    for round_idx in range(1, args.max_rounds + 1):
        success_count, medium_large_count = current_targets(args.index_csv)
        print(
            f"[loop] round={round_idx} current_external_success={success_count} current_medium_large_success={medium_large_count}",
            flush=True,
        )
        if success_count >= args.target_external_success and medium_large_count >= args.target_medium_large_success:
            print("[loop] targets already met", flush=True)
            return

        round_slug = timestamp_slug()
        candidates_csv = args.work_root / f"repo_manifest_candidates_round{round_idx}_{round_slug}.csv"
        candidates_md = args.work_root / f"repo_manifest_candidates_round{round_idx}_{round_slug}.md"
        discover_cmd = [
            PYTHON_BIN,
            str(DISCOVER_REPOS),
            "--min-stars",
            str(args.min_stars),
            "--quality-profile",
            args.quality_profile,
            "--downloads-root",
            str(args.downloads_root),
            "--out-csv",
            str(candidates_csv),
            "--out-md",
            str(candidates_md),
        ]
        for backend in (args.backend or ["github"]):
            discover_cmd.extend(["--backend", backend])
        for keyword in args.keyword:
            discover_cmd.extend(["--keyword", keyword])
        for preferred_size in args.preferred_size:
            discover_cmd.extend(["--preferred-size", preferred_size])
        run(discover_cmd)

        chosen_rows = choose_manifest_rows(
            candidates_csv,
            args.scan_state_json,
            repo_batch_size=args.repo_batch_size,
            allowed_buckets=allowed_buckets,
            include_already_cloned=args.include_already_cloned,
        )
        if not chosen_rows:
            write_round_summary(
                args.work_root / f"search_expand_round{round_idx}_{round_slug}.json",
                round_idx=round_idx,
                keywords=args.keyword,
                approved_manifest=None,
                chosen_rows=[],
                success_before=success_count,
                medium_large_before=medium_large_count,
                success_after=success_count,
                medium_large_after=medium_large_count,
                status="stopped",
                note="no acceptable repo candidates remain",
            )
            print("[loop] no acceptable repo candidates remain", flush=True)
            return

        approved_manifest = args.work_root / f"approved_repo_manifest_round{round_idx}_{round_slug}.csv"
        write_manifest(approved_manifest, chosen_rows)

        run(
            [
                PYTHON_BIN,
                str(RUN_ROUND),
                "--repo-manifest-csv",
                str(approved_manifest),
                "--clone-missing",
                "--discover",
                "--priorities",
                "high",
                "medium",
                "--run-retry",
            ]
        )
        run(
            [
                PYTHON_BIN,
                str(REFRESH_SCAN_STATE),
                "--downloads-root",
                str(args.downloads_root),
                "--audit-csv",
                str(args.audit_csv),
                "--scan-state-json",
                str(args.scan_state_json),
            ]
        )
        run(
            [
                PYTHON_BIN,
                str(SCORE_REPOS),
                "--scan-state-json",
                str(args.scan_state_json),
                "--index-csv",
                str(args.index_csv),
                "--min-repo-success",
                str(args.min_repo_success),
                "--min-medium-large-success",
                str(args.min_medium_large_success),
                "--min-failures-before-reject",
                str(args.min_failures_before_reject),
                "--max-fail-ratio",
                str(args.max_fail_ratio),
                "--out-csv",
                str(args.work_root / f"download_repo_quality_round{round_idx}_{round_slug}.csv"),
                "--out-md",
                str(args.work_root / f"download_repo_quality_round{round_idx}_{round_slug}.md"),
                "--reject-csv",
                str(args.work_root / f"download_repo_rejects_round{round_idx}_{round_slug}.csv"),
                "--quarantine-md",
                str(args.work_root / f"download_repo_quarantine_round{round_idx}_{round_slug}.md"),
            ]
            + (["--reject-if-all-small"] if args.reject_if_all_small else [])
        )

        success_after, medium_large_after = current_targets(args.index_csv)
        delta_success = success_after - success_count
        delta_medium_large = medium_large_after - medium_large_count
        if delta_success <= 0 and delta_medium_large <= 0:
            stalled_rounds += 1
        else:
            stalled_rounds = 0
        write_round_summary(
            args.work_root / f"search_expand_round{round_idx}_{round_slug}.json",
            round_idx=round_idx,
            keywords=args.keyword,
            approved_manifest=approved_manifest,
            chosen_rows=chosen_rows,
            success_before=success_count,
            medium_large_before=medium_large_count,
            success_after=success_after,
            medium_large_after=medium_large_after,
            status="completed",
            note=f"stalled_rounds={stalled_rounds}",
        )
        print(
            f"[loop] end_round={round_idx} external_success={success_after} medium_large_success={medium_large_after} delta_success={delta_success} delta_medium_large={delta_medium_large}",
            flush=True,
        )
        if success_after >= args.target_external_success and medium_large_after >= args.target_medium_large_success:
            print("[loop] targets reached", flush=True)
            return
        if stalled_rounds >= args.max_stalled_rounds:
            print("[loop] stopping after repeated no-progress rounds", flush=True)
            return

    print("[loop] reached max_rounds before targets were met", flush=True)


if __name__ == "__main__":
    main()
