#!/usr/bin/env python3
"""Generate an auditable end-to-end processing funnel and safety report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from frontier import canonical_repository_identity, default_frontier_path, repository_revision_key, utc_now
from functional_ontology import ONTOLOGY_SCHEMA, classify as classify_function


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    corrupt = 0
    if not path.exists():
        return rows, corrupt
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            rows.append(value)
        except json.JSONDecodeError:
            corrupt += 1
    return rows, corrupt


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work" / "data" / "rtl_corpus")
    parser.add_argument("--local-source-root", type=Path, default=Path.home() / "work" / "_downloads")
    parser.add_argument(
        "--source-integrity-mode", choices=("metadata", "full-rehash"),
        default="metadata",
        help="Normal rounds validate recorded immutable references; byte rehash is explicit maintenance only",
    )
    return parser.parse_args()


def git_value(repo: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, timeout=5, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def conservation(stage_input: int, success: int = 0, duplicate: int = 0, quarantine: int = 0, failure: int = 0, skipped: int = 0) -> dict[str, Any]:
    accounted = success + duplicate + quarantine + failure + skipped
    return {
        "stage_input": stage_input, "stage_success": success, "stage_duplicate": duplicate,
        "stage_quarantine": quarantine, "stage_failure": failure, "stage_skipped": skipped,
        "accounted": accounted, "residual": stage_input - accounted, "conserved": stage_input == accounted,
    }


def terminal_suppression_taxonomy(connection: sqlite3.Connection) -> dict[str, int]:
    """Explain every terminally suppressed candidate without an opaque OTHER bucket."""
    rows = connection.execute(
        """WITH latest AS (
             SELECT repository_key,error_class,COALESCE(error_detail,'') error_detail,
                    ROW_NUMBER() OVER(PARTITION BY repository_key ORDER BY completed_at DESC,rowid DESC) rn
             FROM acquisition_attempts
           )
           SELECT l.error_class,l.error_detail,COUNT(*)
           FROM repositories r JOIN latest l ON l.repository_key=r.repository_key AND l.rn=1
           WHERE r.state!='INVALID' AND r.acquisition_status='EXCLUDED'
           GROUP BY l.error_class,l.error_detail"""
    ).fetchall()
    result: Counter[str] = Counter()
    for error_class, detail, count in rows:
        text = str(detail).upper()
        if text.startswith("REVISION_RESOLUTION_FAILED:"):
            category = "REVISION_RESOLUTION_RETRY_EXHAUSTED"
        elif "ARCHIVE_TOO_LARGE" in text:
            category = "ARCHIVE_SIZE_POLICY_EXHAUSTED"
        elif "HTTP" in str(error_class).upper() or "HTTP" in text:
            category = "HTTP_RETRY_EXHAUSTED"
        elif "TIMEOUT" in str(error_class).upper() or "TIMED OUT" in text:
            category = "NETWORK_TIMEOUT_RETRY_EXHAUSTED"
        elif "INTERRUPT" in str(error_class).upper():
            category = "INTERRUPTED_ACQUISITION_TERMINATED"
        elif str(error_class) == "RuntimeError":
            category = "CLASSIFIED_RUNTIME_ACQUISITION_FAILURE"
        else:
            category = f"CLASSIFIED_{str(error_class or 'UNKNOWN').upper()}"
        result[category] += int(count)
    return dict(sorted(result.items()))


def local_intake_funnel(source_root: Path, connection: sqlite3.Connection) -> dict[str, Any]:
    entries = sorted((path for path in source_root.iterdir() if path.is_dir()), key=lambda path: path.name.lower()) if source_root.is_dir() else []
    revisions: Counter[str] = Counter()
    invalid = unresolved = 0
    for repo in entries:
        remote = git_value(repo, ["config", "--get", "remote.origin.url"])
        commit = git_value(repo, ["rev-parse", "HEAD"]).lower()
        if not remote or not commit:
            unresolved += 1
            continue
        try:
            identity = canonical_repository_identity(remote)
            revisions[repository_revision_key(identity["repository_key"], commit)] += 1
        except ValueError:
            invalid += 1
    acquired = {row[0] for row in connection.execute("SELECT repository_revision_key FROM repository_revisions")}
    successful = sum(key in acquired for key in revisions)
    failed_keys = {
        row[0] for row in connection.execute(
            "SELECT DISTINCT repository_key FROM acquisition_attempts WHERE method='local_tracked_snapshot' AND state='FAILED'"
        )
    }
    failed = sum(key.split("@", 1)[0] in failed_keys and key not in acquired for key in revisions)
    skipped = len(revisions) - successful - failed
    duplicates = sum(count - 1 for count in revisions.values())
    return {
        "local_entries_seen": len(entries),
        "unique_revision_candidates": len(revisions),
        "exact_or_local_duplicates": duplicates,
        "invalid_non_repository": invalid,
        "unresolved_provenance": unresolved,
        "immutable_revisions_acquired": successful,
        "acquisition_failures": failed,
        "acquisition_skipped_or_pending": skipped,
        "resolution_conservation": conservation(len(entries), len(revisions), duplicates, invalid, 0, unresolved),
        "acquisition_conservation": conservation(len(revisions), successful, 0, 0, failed, skipped),
    }


def candidate_key(row: dict[str, Any], accepted: bool) -> tuple[str, str, str] | None:
    if accepted:
        return (str(row.get("provenance", {}).get("repo_id")), str(row.get("identity", {}).get("project_key")), str(row.get("build", {}).get("top_module")))
    if not row.get("top_candidate"):
        return None
    return (str(row.get("repo_id")), str(row.get("project_key")), str(row.get("top_candidate")))


def verify_source_hashes(
    designs: list[dict[str, Any]], *, mode: str = "metadata",
) -> dict[str, Any]:
    checked = mismatches = unavailable = 0
    metadata_checked = rehash_required = 0
    for row in designs:
        root_text = row.get("storage", {}).get("repository_source_path") or row.get("source", {}).get("original_root")
        units = row.get("source", {}).get("source_units", [])
        if not root_text or not row.get("source", {}).get("repository_revision_key"):
            unavailable += 1
            continue
        root = Path(root_text)
        for unit in units:
            path = root / unit["path"]
            expected = str(unit.get("sha256") or "").lower()
            metadata_checked += 1
            if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
                rehash_required += 1
                continue
            if not path.is_file():
                rehash_required += 1
                continue
            if mode != "full-rehash":
                continue
            try:
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                actual = "MISSING"
            checked += 1
            mismatches += actual != expected
    return {
        "source_integrity_mode": (
            "EXPLICIT_FULL_REHASH" if mode == "full-rehash"
            else "ADMISSION_DIGEST_METADATA"
        ),
        "source_units_metadata_checked": metadata_checked,
        "source_units_checked": checked,
        "immutable_source_hash_mismatches": mismatches,
        "immutable_source_rehash_required": rehash_required,
        "legacy_unresolved_provenance_designs": unavailable,
    }


def main() -> int:
    args = parse_args()
    manifests = args.corpus_root / "manifests"
    repositories, corrupt_repos = read_jsonl(manifests / "repositories.jsonl")
    designs, corrupt_designs = read_jsonl(manifests / "all_designs.jsonl")
    failures: list[dict[str, Any]] = []
    corrupt_failures = 0
    for path in (args.corpus_root / "failures" / "top_candidates").glob("*.jsonl"):
        rows, corrupt = read_jsonl(path)
        failures.extend(rows)
        corrupt_failures += corrupt
    adjudication_summary: dict[str, Any] = {}
    adjudication_path = args.corpus_root / "quality/phase1_5/failure_audit_summary.json"
    if adjudication_path.is_file():
        try:
            adjudication_summary = json.loads(adjudication_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            adjudication_summary = {}
    project_boundaries = {
        (row.get("provenance", {}).get("repo_id"), row.get("identity", {}).get("project_key")) for row in designs
    } | {(row.get("repo_id"), row.get("project_key")) for row in failures}
    project_boundaries.discard((None, None))
    frontier_counts: dict[str, Any] = {}
    local_funnel: dict[str, Any] = {}
    discovery_conservation: dict[str, Any] = {}
    source_yield: list[dict[str, Any]] = []
    frontier_path = default_frontier_path(args.corpus_root)
    if frontier_path.exists():
        connection = sqlite3.connect(frontier_path)
        connection.row_factory = sqlite3.Row
        for table in ["repositories", "repository_revisions", "discovery_events", "repo_edges", "acquisition_attempts"]:
            frontier_counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        frontier_counts["duplicate_revision_rows"] = connection.execute(
            "SELECT COUNT(*) FROM (SELECT repository_key,commit_sha,COUNT(*) n FROM repository_revisions GROUP BY repository_key,commit_sha HAVING n>1)"
        ).fetchone()[0]
        invalid = connection.execute("SELECT COUNT(*) FROM repositories WHERE state='INVALID'").fetchone()[0]
        acquired = connection.execute("SELECT COUNT(*) FROM repositories WHERE acquisition_status='ACQUIRED'").fetchone()[0]
        retry = connection.execute("SELECT COUNT(*) FROM repositories WHERE state!='INVALID' AND acquisition_status='RETRY'").fetchone()[0]
        excluded = connection.execute("SELECT COUNT(*) FROM repositories WHERE state!='INVALID' AND acquisition_status='EXCLUDED'").fetchone()[0]
        pending = connection.execute("SELECT COUNT(*) FROM repositories WHERE state!='INVALID' AND acquisition_status='NOT_ACQUIRED'").fetchone()[0]
        total_repositories = frontier_counts["repositories"]
        valid_repositories = total_repositories - invalid
        discovery_conservation = {
            "canonicalization": conservation(total_repositories, valid_repositories, 0, invalid, 0, 0),
            "acquisition_state": {
                **conservation(valid_repositories, acquired, 0, excluded, retry, pending),
                "terminal_suppression": terminal_suppression_taxonomy(connection),
                "terminal_suppression_definition": "bounded retry exhausted or explicit acquisition policy exclusion",
            },
        }
        local_funnel = local_intake_funnel(args.local_source_root, connection)

        repository_sources: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for row in connection.execute("SELECT repository_key,provider,strategy FROM discovery_events"):
            repository_sources[row["repository_key"]].add((row["provider"], row["strategy"]))
        query_metrics: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for row in connection.execute("SELECT provider,strategy,SUM(attempts) attempts,SUM(results_seen) seen,SUM(new_repositories) new_keys FROM queries GROUP BY provider,strategy"):
            metrics = query_metrics[(row["provider"], row["strategy"])]
            metrics.update({"queries_run": row["attempts"] or 0, "repositories_seen": row["seen"] or 0, "new_repository_keys": row["new_keys"] or 0})
        revision_sources: dict[str, set[tuple[str, str]]] = {}
        for row in connection.execute("SELECT repository_revision_key,repository_key FROM repository_revisions"):
            revision_sources[row["repository_revision_key"]] = repository_sources.get(row["repository_key"], set())
            for source in revision_sources[row["repository_revision_key"]]:
                query_metrics[source]["acquired_revisions_influenced"] += 1
        bytes_by_repo = dict(connection.execute("SELECT repository_key,SUM(bytes_downloaded) FROM acquisition_attempts WHERE state='ACQUIRED' GROUP BY repository_key").fetchall())
        for repository_key, value in bytes_by_repo.items():
            for source in repository_sources.get(repository_key, set()):
                query_metrics[source]["network_bytes_influenced"] += value or 0
        connection.close()
    split_groups = {row.get("split_group_id") for row in designs}
    family_ids = [row.get("family_id") for row in designs]
    design_ids = [row.get("design_id") for row in designs]
    family_splits: dict[str, set[str]] = {}
    for row in designs:
        family_splits.setdefault(str(row.get("family_id")), set()).add(str(row.get("split")))
    accepted_candidates = {key for row in designs if (key := candidate_key(row, True))}
    rejected_candidates = {key for row in failures if (key := candidate_key(row, False))}
    reported_attempts = sum(int(row.get("top_candidate_attempts", 0)) for row in repositories)
    reconstructed_outcomes = len(accepted_candidates | rejected_candidates)
    failure_runtime = Counter()
    failure_counts = Counter()
    recoverability = {
        "MISSING_INCLUDE": "HIGH", "UNRESOLVED_CHILD": "HIGH", "NO_TOP": "MEDIUM",
        "PARSE_FAIL": "MEDIUM", "GENERIC_SYNTH_FAIL": "MEDIUM", "DUPLICATE_MODULE_DEFINITION": "MEDIUM",
        "TIMEOUT": "LOW_MEDIUM", "REPO_BUDGET_EXHAUSTED": "LOW_MEDIUM",
    }
    for row in failures:
        reason = row.get("failure_type", "UNKNOWN")
        failure_counts[reason] += 1
        failure_runtime[reason] += float(row.get("runtime_seconds", row.get("synthesis", {}).get("runtime_seconds", 0.0)) or 0.0)
    failure_taxonomy = [
        {
            "failure_reason": reason, "count": count,
            "percentage": round(100.0 * count / max(1, len(failures)), 3),
            "observed_tool_hours": round(failure_runtime[reason] / 3600.0, 6),
            "recoverability": recoverability.get(reason, "UNKNOWN"),
            "triage_suspected_recoverable_rate": (
                round(sum(adjudication_summary.get("audit_classes_by_failure", {}).get(reason, {}).get(label, 0) for label in (
                    "BUILD_CONTEXT_RECOVERABLE", "PORTABILITY_RECOVERABLE", "SYNTH_COMPAT_RECOVERABLE"
                )) / max(1, sum(adjudication_summary.get("audit_classes_by_failure", {}).get(reason, {}).values())), 6)
                if reason in adjudication_summary.get("audit_classes_by_failure", {}) else None
            ),
            "estimated_recoverable_probability": None,
            "expected_recoverable_candidates": None,
            "confidence_interval": None,
            "recovery_estimate_status": "UNCALIBRATED_REQUIRES_VALIDATED_REPAIR_AND_PUBLICATION_EVIDENCE",
        }
        for reason, count in failure_counts.most_common()
    ]
    design_sources: dict[str, set[tuple[str, str]]] = defaultdict(set)
    if frontier_path.exists():
        connection = sqlite3.connect(frontier_path)
        for row in designs:
            try:
                key = canonical_repository_identity(row.get("provenance", {}).get("repository_url", ""))["repository_key"]
            except ValueError:
                continue
            sources = repository_sources.get(key, set())
            design_sources[row["design_id"]] = sources
            for source in sources:
                query_metrics[source]["new_design_instances_influenced"] += 1
                query_metrics[source]["cpu_hours_influenced"] += float(row.get("synthesis", {}).get("runtime_seconds", 0.0) or 0.0) / 3600.0
        family_source_sets: dict[str, set[tuple[str, str]]] = defaultdict(set)
        synth_family_source_sets: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for row in designs:
            family_source_sets[row["family_id"]].update(design_sources.get(row["design_id"], set()))
            if row.get("synthesis", {}).get("generic_pass"):
                synth_family_source_sets[row["family_id"]].update(design_sources.get(row["design_id"], set()))
        for sources in family_source_sets.values():
            for source in sources:
                query_metrics[source]["new_families_influenced"] += 1
        for sources in synth_family_source_sets.values():
            for source in sources:
                query_metrics[source]["synthesis_valid_families_influenced"] += 1
        connection.close()
    for (provider, strategy), metrics in sorted(query_metrics.items()):
        families = metrics.get("new_families_influenced", 0)
        source_yield.append({
            "provider": provider, "strategy": strategy, **{key: round(value, 6) for key, value in metrics.items()},
            "revisions_per_synthesis_valid_family": round(metrics.get("acquired_revisions_influenced", 0) / max(1, metrics.get("synthesis_valid_families_influenced", 0)), 6),
            "cpu_hours_per_new_family": round(metrics.get("cpu_hours_influenced", 0) / max(1, families), 6),
        })
    hash_audit = verify_source_hashes(designs, mode=args.source_integrity_mode)
    unresolved_rows = [row for row in designs if not row.get("source", {}).get("repository_revision_key")]
    unresolved_quarantined = sum(
        row.get("release", {}).get("release_policy") == "QUARANTINE"
        and "LEGACY_PROVENANCE_UNRESOLVED" in row.get("quality", {}).get("quality_flags", [])
        for row in unresolved_rows
    )
    language_distribution = Counter(language for row in designs for language in row.get("source", {}).get("source_languages", []))
    resource_distribution = Counter(row.get("resource", {}).get("class", "UNKNOWN") for row in designs)
    function_labels = [classify_function(row) for row in designs]
    category_distribution = Counter(value["label"] for value in function_labels)
    function_confidence = Counter(value["confidence"] for value in function_labels)
    total_families = len(set(family_ids))
    synth_families = {row["family_id"] for row in designs if row.get("synthesis", {}).get("generic_pass")}
    eligible_except_benchmark = {
        row["family_id"] for row in designs
        if row.get("synthesis", {}).get("generic_pass")
        and row.get("quality", {}).get("engineering_quality", 0) >= 65
        and row.get("release", {}).get("release_policy") in {"PUBLIC_EXPORT_ALLOWED", "INTERNAL_TRAINING_ONLY"}
    }
    training_ready = {
        row["family_id"] for row in designs
        if row["family_id"] in eligible_except_benchmark and row.get("contamination", {}).get("audit_status") == "PASS"
    }
    report = {
        "schema": "rtl_processing_scale_pilot_v2", "generated_at": utc_now(),
        "milestone": {"name": "PHASE_1_1K_REPOSITORY_REVISIONS", "target_unique_acquired_repository_revisions": 1000, "current": frontier_counts.get("repository_revisions", 0), "remaining": max(0, 1000 - frontier_counts.get("repository_revisions", 0))},
        "stage_conservation": {"local_intake_cohort": local_funnel, "cumulative_frontier": discovery_conservation},
        "funnel": {
            "repositories": len(repositories),
            "rtl_repositories": sum(row.get("classification") not in {"NO_RTL", None} for row in repositories),
            "project_boundaries_observed": len(project_boundaries),
            "top_candidates_total": len(accepted_candidates | rejected_candidates),
            "top_attempts_reported": reported_attempts,
            "top_attempt_outcomes_reconstructed": reconstructed_outcomes,
            "legacy_attempt_counter_gap": reconstructed_outcomes - reported_attempts,
            "top_candidates_rejected": len(rejected_candidates),
            "top_candidates_accepted": len(accepted_candidates),
            "non_candidate_failure_events": sum(candidate_key(row, False) is None for row in failures),
            "design_instances_emitted": len(designs),
            "design_families": len(set(family_ids)),
            "split_groups": len(split_groups),
            "parse_valid": sum(bool(row.get("validation", {}).get("parse")) for row in designs),
            "elaboration_valid": sum(bool(row.get("validation", {}).get("elaborate")) for row in designs),
        },
        "repository_states": dict(Counter(row.get("state", "UNKNOWN") for row in repositories)),
        "repository_classification": dict(Counter(row.get("classification", "UNKNOWN") for row in repositories)),
        "failure_top10": Counter(row.get("failure_type", "UNKNOWN") for row in failures).most_common(10),
        "failure_taxonomy": failure_taxonomy,
        "provider_strategy_yield": source_yield,
        "diversity_scale": {
            "languages": dict(language_distribution), "resource_classes": dict(resource_distribution),
            "functional_ontology_schema": ONTOLOGY_SCHEMA,
            "functional_categories": dict(category_distribution),
            "functional_label_confidence": dict(function_confidence),
            "functional_residual_misc_ip": category_distribution.get("misc_ip", 0),
        },
        "dashboards": {
            "corpus_scale_unique_design_families": total_families,
            "synthesis_valid_design_families": len(synth_families),
            "engineering_release_eligible_except_benchmark": len(eligible_except_benchmark),
            "training_ready_uncontaminated_families": len(training_ready),
            "benchmark_registry_blocks_gold_premium": any(row.get("contamination", {}).get("audit_status") == "NOT_RUN" for row in designs),
        },
        "repair_levels": dict(Counter(row.get("repair", {}).get("level", "UNKNOWN") for row in designs)),
        "synthesis_classes": dict(Counter(row.get("synthesis", {}).get("status", "UNKNOWN") for row in designs)),
        "release_policy": dict(Counter(row.get("release", {}).get("release_policy", "UNKNOWN") for row in designs)),
        "license_status": dict(Counter(row.get("release", {}).get("license_status", "UNKNOWN") for row in designs)),
        "integrity": {
            "corrupt_manifest_rows": corrupt_repos + corrupt_designs + corrupt_failures,
            "duplicate_design_ids": len(design_ids) - len(set(design_ids)),
            "family_split_violations": sum(len(splits) != 1 for splits in family_splits.values()),
            "split_group_violations": sum(len({row.get("split") for row in designs if row.get("split_group_id") == group}) != 1 for group in split_groups),
            "published_without_elaboration": sum(not row.get("validation", {}).get("elaborate") for row in designs),
            "storage_layout_missing_design_json": sum(not (Path(row.get("storage", {}).get("design_path", "")) / "design.json").is_file() for row in designs),
            **hash_audit,
            "formal_missing_repository_revision": sum(
                not row.get("source", {}).get("repository_revision_key")
                and row.get("release", {}).get("release_policy") != "QUARANTINE"
                for row in designs
            ),
            "legacy_unresolved_provenance_quarantined": unresolved_quarantined,
            "publish_invariants": json.loads((args.corpus_root / "quality" / "publish_invariants.json").read_text()) if (args.corpus_root / "quality" / "publish_invariants.json").exists() else {"valid": False, "reason": "missing"},
            "frontier": frontier_counts,
        },
    }
    output = args.corpus_root / "quality" / "scale_pilot_summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
