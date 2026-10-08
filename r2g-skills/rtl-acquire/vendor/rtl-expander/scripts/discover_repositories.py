#!/usr/bin/env python3
"""Populate the SQLite repository frontier without downloading repositories."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import signal
import subprocess
from pathlib import Path

from discovery_providers import ProviderError, provider_registry
from discovery_evidence import (
    effective_provider_query, precision_policy_round, score_discovery_evidence,
    split_language_date_query,
)
from frontier import FrontierDB, default_frontier_path, utc_now
from scheduler import reprioritize, seed_queries


def candidate_design_likelihood(candidate: dict, query_text: str = "", strategy: str = "") -> float:
    return float(score_discovery_evidence(candidate, query_text=query_text, strategy=strategy)["score"])


def seed_language_coverage_queries(db: FrontierDB, budget: int) -> int:
    """Seed broad language-qualified windows; capped windows split after provider response."""
    windows = [(2008, 2013), (2014, 2018), (2019, 2022), (2023, 2024), (2025, dt.datetime.now(dt.timezone.utc).year)]
    queries = [
        f"language:{language} created:{start}-01-01..{end}-12-31"
        for language in ("Verilog", "SystemVerilog", "VHDL") for start, end in windows
    ]
    per_query = max(1, budget // len(queries))
    for query in queries:
        db.add_query("github", "language_coverage", query, 4.0, per_query)
    return len(queries)


def git_remote(repo: Path) -> str:
    try:
        result = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=repo, text=True, capture_output=True, timeout=5, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def discovery_timeout(_signum: int, _frame: object) -> None:
    raise TimeoutError("DISCOVERY_REQUEST_WALL_CLOCK_EXCEEDED")


def seed_local_repositories(db: FrontierDB, source_root: Path) -> dict[str, int]:
    seen = new = 0
    for repo in sorted((path for path in source_root.iterdir() if path.is_dir()), key=lambda path: path.name.lower()):
        remote = git_remote(repo)
        if not remote:
            continue
        seen += 1
        _, created = db.upsert_repository({"url": remote, "evidence": str(repo)}, "existing_local")
        new += int(created)
    return {"seen": seen, "new": new}


def run_query_round(db: FrontierDB, providers: dict, query_budget: int, page_size: int, query_texts: list[str] | None = None, request_wall_seconds: int = 60, quota_reserve: int = 100, precision_policy: dict | None = None) -> dict[str, int]:
    metrics = {"queries_run": 0, "repositories_seen": 0, "repositories_new": 0, "provider_errors": 0, "provider_cooldown_skips": 0}
    for query in db.ready_queries(query_budget, list(providers), query_texts):
        provider = providers.get(query["provider"])
        if provider is None:
            continue
        if provider.name not in db.discovery_eligible_providers([provider.name], quota_reserve):
            metrics["provider_cooldown_skips"] += 1
            continue
        try:
            signal.alarm(max(1, request_wall_seconds))
            page = provider.search(query["query_text"], query["cursor"], min(page_size, max(1, query["budget"])))
            new_count = 0
            for candidate in page.repositories:
                if candidate.get("archived"):
                    continue
                evidence = score_discovery_evidence(
                    candidate,
                    query_text=effective_provider_query(provider.name, query["query_text"]),
                    strategy=query["strategy"],
                    existing=candidate.get("discovery_evidence"),
                    precision_policy=precision_policy,
                )
                likelihood = float(evidence["score"])
                candidate["design_likelihood"] = likelihood
                candidate["discovery_evidence"] = evidence
                candidate["discovery_score"] = query["priority"]
                adjusted_priority = max(0.0, query["priority"] + likelihood - 0.7)
                _, created = db.upsert_repository(candidate, query["strategy"], query["query_id"], priority=adjusted_priority)
                new_count += int(created)
            metrics["queries_run"] += 1
            metrics["repositories_seen"] += len(page.repositories)
            metrics["repositories_new"] += new_count
            db.record_source_yield(query["provider"], query["strategy"], query["query_text"], len(page.repositories))
            split_queries = split_language_date_query(query["query_text"], int(page.total_count or 0)) if query["strategy"] == "language_coverage" and not query["cursor"] else []
            for child in split_queries:
                db.add_query(query["provider"], "language_coverage", child, query["priority"], query["budget"])
            metrics["language_windows_split"] = metrics.get("language_windows_split", 0) + len(split_queries)
            state = "COMPLETE" if split_queries else "READY" if page.next_cursor else "COMPLETE"
            db.update_query(
                query["query_id"], cursor=page.next_cursor, state=state,
                attempts=query["attempts"] + 1, results_seen=query["results_seen"] + len(page.repositories),
                new_repositories=query["new_repositories"] + new_count, last_error=None,
            )
            db.update_provider_state(
                db.quota_provider(provider.name), cursor=page.next_cursor,
                rate_limit_remaining=page.rate_limit_remaining,
                rate_limit_reset=page.rate_limit_reset,
            )
            db.record_provider_success(
                provider.name, rate_limit_remaining=page.rate_limit_remaining,
                rate_limit_reset=page.rate_limit_reset, source="discovery_query",
            )
        except (ProviderError, TimeoutError) as exc:
            metrics["provider_errors"] += 1
            retry_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(60, getattr(exc, "retry_after", 0)))).replace(microsecond=0).isoformat()
            db.update_query(query["query_id"], state="RETRY", attempts=query["attempts"] + 1, last_error=str(exc), next_run_at=retry_at)
            if isinstance(exc, ProviderError) and exc.rate_limited:
                db.set_provider_rate_limit(
                    provider.name, retry_after=exc.retry_after, reset_at=exc.reset_at,
                    source="discovery_query", detail=str(exc),
                )
        finally:
            signal.alarm(0)
    return metrics


def graph_expand(db: FrontierDB, providers: dict, budget: int, quota_reserve: int = 100) -> dict[str, int]:
    metrics = {"repositories_expanded": 0, "edges_added": 0, "candidates_seen": 0, "expansion_errors": 0, "provider_cooldown_skips": 0}
    if not providers or budget <= 0:
        return metrics
    provider_names = sorted(providers)
    placeholders = ",".join("?" for _ in provider_names)
    rows = db.connection.execute(
        f"""SELECT r.* FROM repositories r LEFT JOIN graph_expansions g USING(repository_key)
           WHERE r.state!='INVALID' AND r.provider IN ({placeholders}) AND COALESCE(g.state,'')!='COMPLETE'
           ORDER BY r.priority DESC,r.last_seen DESC LIMIT ?""", (*provider_names, budget),
    ).fetchall()
    for row in rows:
        provider = providers.get(row["provider"])
        if provider is None:
            continue
        if provider.name not in db.discovery_eligible_providers([provider.name], quota_reserve):
            metrics["provider_cooldown_skips"] += 1
            continue
        try:
            metadata = provider.get_repository_metadata(row["namespace"], row["repo_name"])
            for edge in provider.resolve_upstream(metadata):
                db.add_edge(row["repository_key"], edge["url"], edge["edge_type"], "provider metadata")
                metrics["edges_added"] += 1
            siblings = provider.list_organization_repositories(row["namespace"].split("/")[0], None, 100)
            db.record_provider_success(
                provider.name, rate_limit_remaining=siblings.rate_limit_remaining,
                rate_limit_reset=siblings.rate_limit_reset, source="graph_expansion",
            )
            for candidate in siblings.repositories:
                target, _ = db.upsert_repository(candidate, "organization", source_repository_key=row["repository_key"], priority=max(0, row["priority"] - 0.5))
                if target != row["repository_key"]:
                    db.add_edge(row["repository_key"], candidate["url"], "organization_sibling", "provider organization listing")
                    metrics["edges_added"] += 1
            metrics["candidates_seen"] += len(siblings.repositories)
            metrics["repositories_expanded"] += 1
            db.connection.execute(
                """INSERT INTO graph_expansions(repository_key,state,attempts,last_error,updated_at)
                   VALUES(?,'COMPLETE',1,NULL,?) ON CONFLICT(repository_key) DO UPDATE SET
                   state='COMPLETE',attempts=graph_expansions.attempts+1,last_error=NULL,updated_at=excluded.updated_at""",
                (row["repository_key"], utc_now()),
            )
            db.connection.commit()
        except ProviderError as exc:
            metrics["expansion_errors"] += 1
            if exc.rate_limited:
                db.set_provider_rate_limit(
                    provider.name, retry_after=exc.retry_after, reset_at=exc.reset_at,
                    source="graph_expansion", detail=str(exc),
                )
            db.connection.execute(
                """INSERT INTO graph_expansions(repository_key,state,attempts,last_error,updated_at)
                   VALUES(?,'RETRY',1,?,?) ON CONFLICT(repository_key) DO UPDATE SET
                   state='RETRY',attempts=graph_expansions.attempts+1,last_error=excluded.last_error,updated_at=excluded.updated_at""",
                (row["repository_key"], str(exc), utc_now()),
            )
            db.connection.commit()
            continue
    return metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work" / "data" / "rtl_corpus")
    parser.add_argument("--source-root", type=Path, default=Path.home() / "work" / "_downloads")
    parser.add_argument("--providers", default="github,gitlab,codeberg,fusesoc")
    parser.add_argument("--budget", type=int, default=5000)
    parser.add_argument("--query-budget", type=int, default=50)
    parser.add_argument("--graph-budget", type=int, default=25)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--request-wall-seconds", type=int, default=60)
    parser.add_argument("--quota-reserve", type=int, default=100)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--seed-local", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--controller-round-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    signal.signal(signal.SIGALRM, discovery_timeout)
    selected = [name.strip() for name in args.providers.split(",") if name.strip()]
    providers = provider_registry()
    unknown = sorted(set(selected) - set(providers))
    if unknown:
        raise SystemExit(f"unknown providers: {', '.join(unknown)}")
    with FrontierDB(default_frontier_path(args.corpus_root)) as db:
        precision_policy = (
            db.discovery_precision_policy()
            if precision_policy_round(args.controller_round_id) else None
        )
        db.quarantine_malformed_repositories()
        db.reprioritize_from_edges()
        db.reprioritize_hardware_likelihood(precision_policy)
        local = seed_local_repositories(db, args.source_root) if args.seed_local else {"seen": 0, "new": 0}
        query_count = seed_queries(db, selected, args.budget, args.query or None)
        language_query_count = seed_language_coverage_queries(db, args.budget) if "github" in selected and not args.query else 0
        reprioritize(db)
        if args.dry_run:
            print(json.dumps({"frontier_schema": "rtl_frontier_v1", "queries_seeded": query_count, "local": local, "counts": db.counts()}, indent=2, sort_keys=True))
            return 0
        discovery_selected = db.discovery_eligible_providers(selected, args.quota_reserve)
        active_providers = {name: providers[name] for name in discovery_selected}
        query_metrics = run_query_round(db, active_providers, args.query_budget, args.page_size, args.query or None, args.request_wall_seconds, args.quota_reserve, precision_policy)
        graph_metrics = graph_expand(db, active_providers, args.graph_budget, args.quota_reserve)
        if precision_policy and graph_metrics.get("edges_added", 0):
            db.reprioritize_hardware_likelihood(precision_policy)
        summary = {
            "schema": "rtl_discovery_summary_v2", "timestamp": utc_now(), "local": local,
            "discovery_mode": "PRECISION_RECALIBRATED_V2" if precision_policy else "MULTI_EVIDENCE_V1",
            "precision_policy_schema": precision_policy.get("schema") if precision_policy else None,
            "language_queries_seeded": language_query_count,
            **query_metrics, **graph_metrics, "frontier": db.counts(),
            "quota_reserve": args.quota_reserve,
            "providers_requested": selected,
            "providers_used": discovery_selected,
            "providers_skipped_for_cooldown_or_reservation": sorted(set(selected) - set(discovery_selected)),
            "provider_status": db.provider_statuses(selected, args.quota_reserve),
        }
        output = args.corpus_root / "snapshots" / "discovery_summary.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        temporary.replace(output)
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
