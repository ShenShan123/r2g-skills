#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_downloads_root,
    workspace_path,
)

DEFAULT_OUT_CSV = workspace_path("manifests/repo_manifest_candidates.csv")
DEFAULT_OUT_MD = workspace_path("manifests/repo_manifest_candidates.md")
DEFAULT_DOWNLOADS = default_downloads_root()
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITLAB_SEARCH_URL = "https://gitlab.com/api/v4/projects"
GITEE_SEARCH_URL = "https://gitee.com/api/v5/search/repositories"

POSITIVE_KEYWORDS = {
    "ethernet",
    "axi",
    "axil",
    "wishbone",
    "wb",
    "usb",
    "uart",
    "spi",
    "i2c",
    "bridge",
    "interconnect",
    "crossbar",
    "dma",
    "controller",
    "core",
    "mac",
    "udp",
    "ip",
    "riscv",
    "verilog",
    "rtl",
}

NEGATIVE_KEYWORDS = {
    "test",
    "tb",
    "uvm",
    "cocotb",
    "verification",
    "driver",
    "firmware",
    "software",
    "toolchain",
    "litex",
    "linux",
    "os",
}

MEDIUM_SIZE_HINTS = {
    "controller",
    "bridge",
    "adapter",
    "cdc",
    "register",
    "fifo",
    "uart",
    "spi",
    "i2c",
    "irq",
    "usb",
}

LARGE_SIZE_HINTS = {
    "ethernet",
    "crossbar",
    "interconnect",
    "dma",
    "complete",
    "mac",
    "udp",
    "ip",
    "cpu",
    "riscv",
    "axi",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def infer_dest_name(repo_url: str) -> str:
    name = repo_url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def json_get(url: str, *, headers: dict[str, str] | None = None) -> dict | list:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "nangate45-graph-expander",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def github_search(query: str, *, per_page: int, page: int) -> list[dict]:
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": str(per_page),
        "page": str(page),
    }
    url = f"{GITHUB_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    payload = json_get(url, headers={"Accept": "application/vnd.github+json"})
    return payload.get("items", [])


def gitlab_search(query: str, *, per_page: int, page: int, base_url: str) -> list[dict]:
    params = {
        "search": query,
        "simple": "true",
        "order_by": "star_count",
        "sort": "desc",
        "per_page": str(per_page),
        "page": str(page),
    }
    url = f"{base_url.rstrip('/')}/api/v4/projects?{urllib.parse.urlencode(params)}"
    payload = json_get(url)
    return payload if isinstance(payload, list) else []


def gitee_search(query: str, *, per_page: int, page: int, base_url: str) -> list[dict]:
    params = {
        "q": query,
        "sort": "stars_count",
        "order": "desc",
        "per_page": str(per_page),
        "page": str(page),
    }
    url = f"{base_url.rstrip('/')}/api/v5/search/repositories?{urllib.parse.urlencode(params)}"
    payload = json_get(url)
    return payload if isinstance(payload, list) else []


def repo_score(item: dict, *, preferred_sizes: set[str], quality_profile: str) -> tuple[int, str, str]:
    name = normalize_text(item.get("name", ""))
    desc = normalize_text(item.get("description", ""))
    full = normalize_text(item.get("full_name", ""))
    text = " ".join(filter(None, [name, desc, full]))
    stars = int(item.get("stargazers_count", 0))

    positive = sum(1 for token in POSITIVE_KEYWORDS if token in text)
    negative = sum(1 for token in NEGATIVE_KEYWORDS if token in text)

    medium_hits = sum(1 for token in MEDIUM_SIZE_HINTS if token in text)
    large_hits = sum(1 for token in LARGE_SIZE_HINTS if token in text)

    size_guess = "unknown"
    if large_hits > medium_hits and large_hits > 0:
        size_guess = "large"
    elif medium_hits > 0:
        size_guess = "medium"

    score = positive * 10 - negative * 12 + min(stars, 200)
    if quality_profile == "pure_rtl":
        if "verilog" in text or "rtl" in text:
            score += 15
        if "systemverilog" in text:
            score -= 5
    if preferred_sizes:
        if size_guess in preferred_sizes:
            score += 20
        elif size_guess != "unknown":
            score -= 5

    rationale_bits: list[str] = []
    if stars:
        rationale_bits.append(f"stars={stars}")
    if positive:
        rationale_bits.append(f"positive_keywords={positive}")
    if negative:
        rationale_bits.append(f"negative_keywords={negative}")
    if size_guess != "unknown":
        rationale_bits.append(f"size_guess={size_guess}")
    rationale = "; ".join(rationale_bits) if rationale_bits else "heuristic_score_only"
    return score, size_guess, rationale


def quality_bucket(score: int) -> str:
    if score >= 80:
        return "recommended_now"
    if score >= 45:
        return "review"
    return "conditional"


def make_query(keywords: list[str], language: str) -> str:
    parts = [kw.strip() for kw in keywords if kw.strip()]
    if language:
        parts.append(f"language:{language}")
    return " ".join(parts)


def canonical_item(item: dict, *, backend: str) -> dict[str, object]:
    if backend == "github":
        return {
            "repo_url": item.get("html_url", ""),
            "full_name": item.get("full_name", ""),
            "name": item.get("name", ""),
            "description": item.get("description", "") or "",
            "stars": int(item.get("stargazers_count", 0) or 0),
            "default_branch": item.get("default_branch", ""),
            "pushed_at": item.get("pushed_at", ""),
            "backend": backend,
        }
    if backend == "gitlab":
        web_url = item.get("web_url", "")
        return {
            "repo_url": web_url,
            "full_name": item.get("path_with_namespace", "") or item.get("name_with_namespace", "") or item.get("name", ""),
            "name": item.get("path", "") or item.get("name", ""),
            "description": item.get("description", "") or "",
            "stars": int(item.get("star_count", 0) or 0),
            "default_branch": item.get("default_branch", "") or "main",
            "pushed_at": item.get("last_activity_at", ""),
            "backend": backend,
        }
    if backend == "gitee":
        web_url = item.get("html_url", "")
        full_name = item.get("full_name", "") or f"{item.get('namespace', {}).get('path', '')}/{item.get('path', '')}".strip("/")
        return {
            "repo_url": web_url,
            "full_name": full_name,
            "name": item.get("path", "") or item.get("name", ""),
            "description": item.get("description", "") or "",
            "stars": int(item.get("stargazers_count", 0) or item.get("stars_count", 0) or 0),
            "default_branch": item.get("default_branch", "") or "master",
            "pushed_at": item.get("pushed_at", ""),
            "backend": backend,
        }
    raise ValueError(f"unsupported backend: {backend}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Search code-hosting backends and generate a repo_manifest_candidates shortlist for graph expansion.")
    parser.add_argument("--keyword", action="append", default=[], help="Keyword query. May be repeated.")
    parser.add_argument("--min-stars", type=int, default=5)
    parser.add_argument("--language", default="Verilog")
    parser.add_argument("--backend", action="append", choices=["github", "gitlab", "gitee"], default=[])
    parser.add_argument("--gitlab-base-url", default="https://gitlab.com")
    parser.add_argument("--gitee-base-url", default="https://gitee.com")
    parser.add_argument("--preferred-size", choices=["medium", "large"], action="append", default=[])
    parser.add_argument("--quality-profile", choices=["pure_rtl", "broad"], default="pure_rtl")
    parser.add_argument("--per-query-limit", type=int, default=20)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    if not args.keyword:
        raise SystemExit("at least one --keyword is required")

    backends = args.backend or ["github"]
    preferred_sizes = set(args.preferred_size)
    by_repo: dict[str, dict] = {}
    by_query_count: dict[str, int] = defaultdict(int)
    backend_failures: list[dict[str, str]] = []

    for backend in backends:
        for keyword in args.keyword:
            query = make_query([keyword], args.language)
            for page in range(1, args.pages + 1):
                try:
                    if backend == "github":
                        items = github_search(query, per_page=min(args.per_query_limit, 100), page=page)
                    elif backend == "gitlab":
                        items = gitlab_search(query, per_page=min(args.per_query_limit, 100), page=page, base_url=args.gitlab_base_url)
                    elif backend == "gitee":
                        items = gitee_search(query, per_page=min(args.per_query_limit, 100), page=page, base_url=args.gitee_base_url)
                    else:
                        items = []
                except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                    failure = {
                        "backend": str(backend),
                        "keyword": str(keyword),
                        "page": str(page),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                    backend_failures.append(failure)
                    print(
                        "[discover][warning] backend query failed: "
                        f"backend={backend} keyword={keyword!r} page={page} "
                        f"error_type={type(exc).__name__} error={exc}",
                        flush=True,
                    )
                    continue
                for raw_item in items:
                    item = canonical_item(raw_item, backend=backend)
                    stars = int(item.get("stars", 0))
                    if stars < args.min_stars:
                        continue
                    repo_url = str(item.get("repo_url", ""))
                    if not repo_url:
                        continue
                    full_name = str(item.get("full_name", ""))
                    name = str(item.get("name", ""))
                    desc = str(item.get("description", ""))
                    score, size_guess, rationale = repo_score(
                        {
                            "name": name,
                            "description": desc,
                            "full_name": full_name,
                            "stargazers_count": stars,
                        },
                        preferred_sizes=preferred_sizes,
                        quality_profile=args.quality_profile,
                    )
                    existing = by_repo.get(full_name)
                    entry = {
                        "source_type": "git",
                        "source_url": repo_url,
                        "repo_url": repo_url,
                        "dest_name": infer_dest_name(repo_url),
                        "branch": str(item.get("default_branch", "")),
                        "depth": "1",
                        "notes": f"discovered_from={keyword}; backend={backend}; score={score}; {rationale}",
                        "full_name": full_name,
                        "description": desc,
                        "stars": stars,
                        "query": keyword,
                        "backend": backend,
                        "quality_bucket": quality_bucket(score),
                        "size_guess": size_guess,
                        "score": score,
                        "already_cloned": str((args.downloads_root / infer_dest_name(repo_url)).exists()).lower(),
                        "clone_decision": "pending_review",
                        "pushed_at": str(item.get("pushed_at", "")),
                    }
                    if existing is None or entry["score"] > existing["score"]:
                        by_repo[full_name] = entry
                    by_query_count[f"{backend}:{keyword}"] += 1

    rows = sorted(by_repo.values(), key=lambda row: (-int(row["score"]), -int(row["stars"]), row["full_name"]))
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "repo_url",
                "source_type",
                "source_url",
                "dest_name",
                "branch",
                "depth",
                "notes",
                "full_name",
                "description",
                "stars",
                "query",
                "backend",
                "quality_bucket",
                "size_guess",
                "score",
                "already_cloned",
                "clone_decision",
                "pushed_at",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    counts = defaultdict(int)
    for row in rows:
        counts[row["quality_bucket"]] += 1

    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Repo Manifest Candidates\n\n")
        fh.write(f"- generated_at: {now_iso()}\n")
        fh.write(f"- keywords: {', '.join(args.keyword)}\n")
        fh.write(f"- min_stars: {args.min_stars}\n")
        fh.write(f"- language: {args.language}\n")
        fh.write(f"- backends: {', '.join(backends)}\n")
        fh.write(f"- preferred_size: {', '.join(sorted(preferred_sizes)) or 'any'}\n")
        fh.write(f"- quality_profile: {args.quality_profile}\n")
        fh.write(f"- candidate_count: {len(rows)}\n")
        fh.write(f"- backend_failures: {len(backend_failures)}\n")
        fh.write(f"- recommended_now: {counts['recommended_now']}\n")
        fh.write(f"- review: {counts['review']}\n")
        fh.write(f"- conditional: {counts['conditional']}\n")
        fh.write("\n## Top Candidates\n\n")
        fh.write("| repo | stars | quality | size_guess | already_cloned | why |\n")
        fh.write("|---|---:|---|---|---|---|\n")
        for row in rows[: min(len(rows), 30)]:
            fh.write(
                f"| {row['full_name']} | {row['stars']} | {row['quality_bucket']} | {row['size_guess']} | {row['already_cloned']} | {row['notes']} |\n"
            )
        if backend_failures:
            fh.write("\n## Backend Query Failures\n\n")
            fh.write("| backend | keyword | page | error_type | error |\n")
            fh.write("|---|---|---:|---|---|\n")
            for failure in backend_failures[:50]:
                err = failure["error"].replace("|", "\\|")
                fh.write(
                    f"| {failure['backend']} | {failure['keyword']} | {failure['page']} | {failure['error_type']} | {err} |\n"
                )
        fh.write("\n## Usage\n\n")
        fh.write("This file is a shortlist only. It does not clone anything by itself.\n\n")
        fh.write("After review, copy the approved rows into a final repo manifest CSV and run.\n")
        fh.write("The final manifest may mix GitHub and non-GitHub sources.\n")
        fh.write("For non-GitHub sources, set `source_type=git` with `repo_url/source_url`, or `source_type=archive` with `archive_url`.\n\n")
        fh.write("```bash\n")
        fh.write(f"{sys.executable} {_SKILL_SCRIPTS_DIR / 'run_expansion_round.py'} \\\n")
        fh.write(f"  --repo-manifest-csv {DEFAULT_OUT_CSV.parent}/<approved_repo_manifest>.csv \\\n")
        fh.write("  --clone-missing \\\n")
        fh.write("  --discover \\\n")
        fh.write("  --priorities high medium low \\\n")
        fh.write("  --run-retry\n")
        fh.write("```\n")

    print(f"wrote {args.out_csv}")
    print(f"wrote {args.out_md}")
    print(f"candidate_count {len(rows)}")


if __name__ == "__main__":
    main()
