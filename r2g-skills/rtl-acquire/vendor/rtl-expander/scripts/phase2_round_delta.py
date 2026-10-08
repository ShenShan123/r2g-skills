#!/usr/bin/env python3
"""Capture and finalize auditable Phase-2 factory-round marginal-yield reports."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from frontier import canonical_repository_identity
from processing_queue import ProcessingQueue
from scheduler import query_features


SCHEMA = "rtl_phase2_round_delta_v1"
COHORT_LOCK_SCHEMA = "rtl_phase2_acquisition_cohort_lock_v1"
TERMINAL_REPOSITORY_STATES = {"NO_RTL", "NO_DESIGN", "SYNTH_VALID", "DESIGN_RECOVERED"}


def object_sha256(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(material).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return values


def atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def cohort_lock_path(corpus: Path, round_id: str) -> Path:
    return corpus / "quality/phase2/rounds" / round_id / "cohort_lock.json"


def select_cohort_revisions(
    corpus: Path, start: dict[str, Any], end_revisions: set[str]
) -> tuple[set[str], dict[str, Any] | None]:
    """Use a frozen cohort when present; otherwise retain legacy end-minus-start behavior."""
    operational = end_revisions - set(start["revision_keys"])
    path = cohort_lock_path(corpus, str(start["factory_round_id"]))
    if not path.is_file():
        return operational, None
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("schema") != COHORT_LOCK_SCHEMA:
        raise ValueError(f"unsupported Phase-2 cohort lock schema: {lock.get('schema')}")
    if lock.get("factory_round_id") != start.get("factory_round_id"):
        raise ValueError("Phase-2 cohort lock belongs to a different factory round")
    locked_list = list(map(str, lock.get("revision_keys", [])))
    locked = set(locked_list)
    if locked_list != sorted(locked_list) or len(locked_list) != len(locked):
        raise ValueError("Phase-2 cohort lock keys must be unique and sorted")
    if len(locked) != int(lock.get("acquired_revision_count", -1)):
        raise ValueError("Phase-2 cohort lock count does not match its revision keys")
    if int(lock.get("actual_cohort_size", len(locked))) != len(locked):
        raise ValueError("Phase-2 cohort lock actual size does not match its revision keys")
    if lock.get("early_close") is True:
        evidence = lock.get("early_close_evidence") or {}
        if lock.get("early_close_reason") != "ELIGIBLE_PRODUCTION_FRONTIER_EXHAUSTED":
            raise ValueError("Phase-2 cohort lock has an invalid early-close reason")
        if evidence.get("eligible") is not True or not all(
            (evidence.get("checks") or {}).values()
        ):
            raise ValueError("Phase-2 cohort lock has invalid early-close evidence")
    material = "\n".join(locked_list) + "\n"
    if hashlib.sha256(material.encode()).hexdigest() != lock.get("revision_keys_sha256"):
        raise ValueError("Phase-2 cohort lock revision-key hash mismatch")
    if locked & set(start["revision_keys"]):
        raise ValueError("Phase-2 cohort lock contains a pre-round revision")
    missing = locked - end_revisions
    if missing:
        raise ValueError(f"Phase-2 cohort lock contains {len(missing)} unknown revisions")
    return locked, lock


def revision_key_from_repository(row: dict[str, Any]) -> str | None:
    url, commit = str(row.get("repository_url") or ""), str(row.get("commit_sha") or "")
    if not url or url == "UNKNOWN" or not commit or commit == "UNKNOWN":
        return None
    try:
        return f"{canonical_repository_identity(url)['repository_key']}@{commit.lower()}"
    except ValueError:
        return None


def terminal_revision_keys(repositories: Iterable[dict[str, Any]]) -> set[str]:
    return {
        key
        for row in repositories
        if (key := revision_key_from_repository(row))
        and str(row.get("state")) in TERMINAL_REPOSITORY_STATES
    }


def locked_processing_context(
    corpus: Path, round_id: str, cohort_revisions: set[str]
) -> tuple[set[str], list[dict[str, Any]]] | None:
    """Return round-local terminal truth for a pipelined locked cohort.

    Incremental finalization does not require newly processed repository rows to
    exist in the legacy repositories compatibility view.  When this round owns
    processing-queue rows, those rows are therefore the terminal-set authority.
    Only an exactly terminal cohort is allowed to expose its staged repository
    payloads, and those payloads are loaded through the same strict identity
    validation used by the incremental finalizer.
    """
    database = corpus / "state/corpus.sqlite"
    if not database.is_file():
        return None
    with ProcessingQueue(corpus) as queue:
        rows = queue.rows(round_id)
    if not rows:
        return None
    row_keys = {str(row["repository_revision_key"]) for row in rows}
    terminal = {
        str(row["repository_revision_key"])
        for row in rows if str(row.get("state")) == "TERMINAL"
    }
    if row_keys == cohort_revisions and terminal == cohort_revisions:
        # Local import avoids coupling legacy delta capture to the incremental
        # publisher unless a pipelined round actually uses this context.
        from finalize_staged_round import staged_payloads

        repositories, _ = staged_payloads(corpus, round_id, cohort_revisions)
        return terminal, repositories
    # Keep a partially drained or mismatched queue provisional.  Unexpected
    # round keys must never count toward locked-cohort coverage.
    return terminal & cohort_revisions, []


def failure_id(row: dict[str, Any]) -> str:
    material = "\0".join(str(row.get(key, "")) for key in ("repo_id", "project_key", "top_candidate", "failure_type", "timestamp"))
    return hashlib.sha256(material.encode()).hexdigest()


def all_failures(corpus: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for path in sorted((corpus / "failures/top_candidates").glob("*.jsonl")):
        values.extend(jsonl(path))
    return values


def capture(corpus: Path, round_id: str) -> dict[str, Any]:
    designs = jsonl(corpus / "manifests/all_designs.jsonl")
    repositories = jsonl(corpus / "manifests/repositories.jsonl")
    failures = all_failures(corpus)
    db = sqlite3.connect(corpus / "state/frontier.sqlite")
    revisions = [row[0] for row in db.execute("SELECT repository_revision_key FROM repository_revisions ORDER BY repository_revision_key")]
    db.close()
    gold_families = {row["family_id"] for row in designs if row.get("quality", {}).get("training_tier") == "TRAINING_GOLD"}
    snapshot = {
        "schema": SCHEMA, "snapshot_role": "START", "factory_round_id": round_id,
        "captured_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "revision_keys": revisions,
        "processed_revision_keys": sorted(filter(None, (revision_key_from_repository(row) for row in repositories))),
        "repository_ids": sorted(str(row.get("repo_id")) for row in repositories),
        "design_ids": sorted(str(row.get("design_id")) for row in designs),
        "family_ids": sorted({str(row.get("family_id")) for row in designs}),
        "gold_family_ids": sorted(gold_families),
        "failure_ids": sorted(failure_id(row) for row in failures),
        "project_targets": sorted({f"{row.get('provenance', {}).get('repo_id')}:{row.get('identity', {}).get('project_key')}" for row in designs} | {f"{row.get('repo_id')}:{row.get('project_key')}" for row in failures}),
        "counts": {"repository_revisions": len(revisions), "processed_revisions": len(repositories), "design_instances": len(designs), "design_families": len({row.get('family_id') for row in designs}), "gold_families": len(gold_families)},
    }
    atomic(corpus / "quality/phase2/rounds" / round_id / "start.json", snapshot)
    return snapshot


def backfill_start(corpus: Path, round_id: str, acquired_count: int, processing_run: Path) -> dict[str, Any]:
    """Reconstruct a prior start only when its acquisition size and processing run are explicit."""
    designs = jsonl(corpus / "manifests/all_designs.jsonl")
    repositories = jsonl(corpus / "manifests/repositories.jsonl")
    failures = all_failures(corpus)
    run = json.loads(processing_run.read_text(encoding="utf-8"))
    new_repo_ids = {str(row.get("repo_id")) for row in run.get("repositories", []) if row.get("repo_id")}
    new_design_ids = {str(row["design_id"]) for row in designs if str(row.get("provenance", {}).get("repo_id")) in new_repo_ids}
    old_designs = [row for row in designs if str(row["design_id"]) not in new_design_ids]
    old_failures = [row for row in failures if str(row.get("repo_id")) not in new_repo_ids]
    db = sqlite3.connect(corpus / "state/frontier.sqlite")
    ordered = [row[0] for row in db.execute("SELECT repository_revision_key FROM repository_revisions ORDER BY acquired_at DESC,repository_revision_key DESC")]
    db.close()
    if acquired_count <= 0 or acquired_count > len(ordered):
        raise ValueError("invalid explicit acquired-count for backfill")
    revision_keys = sorted(set(ordered[acquired_count:]))
    processed_now = set(filter(None, (revision_key_from_repository(row) for row in repositories)))
    processed_new = {key for key, row in ((revision_key_from_repository(row), row) for row in repositories) if key and str(row.get("repo_id")) in new_repo_ids}
    families = {str(row["family_id"]) for row in old_designs}
    gold = {str(row["family_id"]) for row in old_designs if row.get("quality", {}).get("training_tier") == "TRAINING_GOLD"}
    snapshot = {
        "schema": SCHEMA, "snapshot_role": "BACKFILLED_START", "factory_round_id": round_id,
        "captured_at": run.get("started_and_completed_at"), "backfill_evidence": {"acquired_count": acquired_count, "processing_run": str(processing_run), "processed_repository_ids": sorted(new_repo_ids)},
        "revision_keys": revision_keys, "processed_revision_keys": sorted(processed_now - processed_new),
        "repository_ids": sorted(str(row.get("repo_id")) for row in repositories if str(row.get("repo_id")) not in new_repo_ids),
        "design_ids": sorted(str(row["design_id"]) for row in old_designs), "family_ids": sorted(families), "gold_family_ids": sorted(gold),
        "failure_ids": sorted(failure_id(row) for row in old_failures),
        "project_targets": sorted({f"{row.get('provenance', {}).get('repo_id')}:{row.get('identity', {}).get('project_key')}" for row in old_designs} | {f"{row.get('repo_id')}:{row.get('project_key')}" for row in old_failures}),
        "counts": {"repository_revisions": len(revision_keys), "processed_revisions": len(repositories) - len(new_repo_ids), "design_instances": len(old_designs), "design_families": len(families), "gold_families": len(gold)},
    }
    atomic(corpus / "quality/phase2/rounds" / round_id / "start.json", snapshot)
    return snapshot


def query_family(text: str, strategy: str) -> str:
    features = sorted(query_features(text))
    if features:
        return "+".join(features)
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return normalized[:120] if normalized else f"{strategy}:unattributed"


def attribution(db: sqlite3.Connection, revision_keys: Iterable[str]) -> tuple[dict[str, tuple[str, str, str]], dict[str, list[dict[str, str]]]]:
    primary: dict[str, tuple[str, str, str]] = {}
    touches: dict[str, list[dict[str, str]]] = {}
    for revision_key in revision_keys:
        repository_key = revision_key.rsplit("@", 1)[0]
        events = db.execute(
            """SELECT de.provider,de.strategy,COALESCE(q.query_text,''),de.event_id
               FROM discovery_events de LEFT JOIN queries q USING(query_id)
               WHERE de.repository_key=? ORDER BY de.discovered_at,de.event_id""", (repository_key,),
        ).fetchall()
        values = [{"provider": row[0], "strategy": row[1], "query_family": query_family(row[2], row[1]), "event_id": row[3]} for row in events]
        if not values:
            provider = repository_key.split(":", 1)[0]
            values = [{"provider": provider, "strategy": "UNATTRIBUTED", "query_family": "UNATTRIBUTED", "event_id": "UNAVAILABLE"}]
        first = values[0]
        primary[revision_key] = (first["provider"], first["strategy"], first["query_family"])
        touches[revision_key] = values
    return primary, touches


def admission_anchors(db: sqlite3.Connection, revision_keys: Iterable[str]) -> dict[str, str]:
    """Return the frozen discovery admission class for each immutable revision."""
    result: dict[str, str] = {}
    for revision_key in revision_keys:
        repository_key = revision_key.rsplit("@", 1)[0]
        row = db.execute(
            "SELECT metadata_json FROM repositories WHERE repository_key=?", (repository_key,),
        ).fetchone()
        try:
            metadata = json.loads(row[0] or "{}") if row else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        result[revision_key] = str(
            (metadata.get("discovery_evidence") or {}).get("admission_anchor") or "UNANCHORED"
        )
    return result


def finalize(corpus: Path, start: dict[str, Any], stages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    output = corpus / "quality/phase2/rounds" / start["factory_round_id"] / "phase2_round_delta_summary.json"
    lock_path = cohort_lock_path(corpus, str(start["factory_round_id"]))
    if output.is_file() and lock_path.is_file():
        existing = json.loads(output.read_text(encoding="utf-8"))
        if (
            existing.get("yield_status") == "FINAL"
            and (existing.get("cohort_lock") or {}).get("sha256") == file_sha256(lock_path)
            and existing.get("start_snapshot_sha256") == object_sha256(start)
        ):
            atomic(corpus / "quality/phase2/phase2_round_delta_summary.json", existing)
            return existing
    designs = jsonl(corpus / "manifests/all_designs.jsonl")
    repositories = jsonl(corpus / "manifests/repositories.jsonl")
    failures = all_failures(corpus)
    start_revisions, start_designs = set(start["revision_keys"]), set(start["design_ids"])
    start_families, start_gold = set(start["family_ids"]), set(start["gold_family_ids"])
    start_failures, start_targets = set(start["failure_ids"]), set(start["project_targets"])
    designs_by_id = {str(row["design_id"]): row for row in designs}
    repos_by_revision = {key: row for row in repositories if (key := revision_key_from_repository(row))}
    terminal_revisions = terminal_revision_keys(repositories)
    db = sqlite3.connect(corpus / "state/frontier.sqlite")
    revision_rows = db.execute("SELECT repository_revision_key FROM repository_revisions ORDER BY repository_revision_key").fetchall()
    end_revisions = {row[0] for row in revision_rows}
    operational_new_revisions = end_revisions - start_revisions
    cohort_revisions, cohort_lock = select_cohort_revisions(corpus, start, end_revisions)
    processing_context = locked_processing_context(
        corpus, str(start["factory_round_id"]), cohort_revisions,
    )
    processing_identity_source = "REPOSITORY_COMPATIBILITY_VIEW"
    if processing_context is not None:
        queue_terminal_revisions, staged_repositories = processing_context
        cohort_terminal_revisions = queue_terminal_revisions
        for row in staged_repositories:
            key = str(row.get("repository_revision_key") or "")
            if key in cohort_revisions:
                repos_by_revision[key] = row
        processing_identity_source = "LOCKED_PROCESSING_QUEUE_AND_RUN_ARTIFACTS"
    else:
        cohort_terminal_revisions = cohort_revisions & terminal_revisions
    primary, touches = attribution(db, sorted(cohort_revisions))
    anchor_by_revision = admission_anchors(db, sorted(cohort_revisions))
    network_bytes_by_revision = {
        revision: int(db.execute("SELECT COALESCE(SUM(bytes_downloaded),0) FROM acquisition_attempts WHERE repository_revision_key=?", (revision,)).fetchone()[0])
        for revision in cohort_revisions
    }
    db.close()
    new_design_rows = [designs_by_id[value] for value in sorted(set(designs_by_id) - start_designs)]
    new_failure_rows = [row for row in failures if failure_id(row) not in start_failures]
    repo_id_to_revision = {str(row.get("repo_id")): key for key, row in repos_by_revision.items()}
    cohort_designs = [row for row in new_design_rows if repo_id_to_revision.get(str(row.get("provenance", {}).get("repo_id"))) in cohort_revisions]
    nonterminal_revisions = cohort_revisions - cohort_terminal_revisions
    terminal_set_matches = cohort_terminal_revisions == cohort_revisions
    cohort_repo_ids = {str(repos_by_revision[key].get("repo_id")) for key in cohort_terminal_revisions}
    cohort_failures = [row for row in new_failure_rows if str(row.get("repo_id")) in cohort_repo_ids]
    new_families = {str(row["family_id"]) for row in new_design_rows} - start_families
    cohort_new_families = {str(row["family_id"]) for row in cohort_designs} & new_families
    end_gold = {str(row["family_id"]) for row in designs if row.get("quality", {}).get("training_tier") == "TRAINING_GOLD"}
    new_gold = end_gold - start_gold
    cohort_new_gold = {str(row["family_id"]) for row in cohort_designs} & new_gold
    duplicate_family_designs = [row for row in cohort_designs if str(row["family_id"]) in start_families]
    target_values = {f"{row.get('provenance', {}).get('repo_id')}:{row.get('identity', {}).get('project_key')}" for row in cohort_designs} | {f"{row.get('repo_id')}:{row.get('project_key')}" for row in cohort_failures}
    new_targets = target_values - start_targets
    attribution_rows: defaultdict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    anchor_rows: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for revision in cohort_revisions:
        anchor = anchor_by_revision[revision]
        anchor_rows[anchor]["new_acquired_revisions"] += 1
        attribution_rows[primary[revision]]["new_acquired_revisions"] += 1
        attribution_rows[primary[revision]]["network_bytes"] += network_bytes_by_revision[revision]
        if revision in cohort_terminal_revisions:
            anchor_rows[anchor]["processed_revisions"] += 1
            attribution_rows[primary[revision]]["processed_revisions"] += 1
            repo = repos_by_revision[revision]
            classification = str(repo.get("classification", "UNKNOWN"))
            state = str(repo.get("state", "UNKNOWN"))
            outcome_detail = str(repo.get("repository_outcome_detail", "LEGACY_UNCLASSIFIED"))
            attribution_rows[primary[revision]][f"classification:{classification}"] += 1
            attribution_rows[primary[revision]][f"state:{state}"] += 1
            attribution_rows[primary[revision]][f"outcome:{outcome_detail}"] += 1
            anchor_rows[anchor][f"outcome:{outcome_detail}"] += 1
            if classification == "NO_RTL":
                anchor_rows[anchor]["no_rtl_revisions"] += 1
    for row in cohort_designs:
        revision = repo_id_to_revision.get(str(row.get("provenance", {}).get("repo_id")))
        if revision in primary:
            anchor_rows[anchor_by_revision[revision]]["new_design_instances"] += 1
            attribution_rows[primary[revision]]["new_design_instances"] += 1
            attribution_rows[primary[revision]]["cpu_seconds"] += float(row.get("synthesis", {}).get("runtime_seconds") or 0.0)
            resource_class = str(row.get("resource", {}).get("class") or "UNKNOWN")
            attribution_rows[primary[revision]][f"resource:{resource_class}"] += 1
            if str(row.get("family_id")) in start_families:
                attribution_rows[primary[revision]]["duplicate_existing_family_design_instances"] += 1
    # Give every new family exactly one deterministic primary-revision credit.
    for family in cohort_new_families:
        revisions = sorted({repo_id_to_revision.get(str(row.get("provenance", {}).get("repo_id"))) for row in cohort_designs if str(row["family_id"]) == family} - {None})
        if revisions:
            anchor = anchor_by_revision[revisions[0]]
            anchor_rows[anchor]["new_design_families"] += 1
            attribution_rows[primary[revisions[0]]]["new_design_families"] += 1
            if family in cohort_new_gold:
                anchor_rows[anchor]["new_gold_families"] += 1
                attribution_rows[primary[revisions[0]]]["new_gold_families"] += 1
    processed_count = len(cohort_terminal_revisions)
    coverage = processed_count / max(1, len(cohort_revisions))
    yield_status = "FINAL" if cohort_revisions and terminal_set_matches else "PROVISIONAL_PENDING_PROCESSING"
    resource = Counter(row.get("resource", {}).get("class", "UNKNOWN") for row in cohort_designs)
    complex_labels = {"noc", "pcie", "accelerator", "soc", "cpu", "cache", "memory_controller"}
    attribution_path = corpus / "quality/phase2/rounds" / start["factory_round_id"] / "attribution_evidence.json"
    atomic(attribution_path, {"schema": "rtl_round_multi_touch_attribution_v1", "factory_round_id": start["factory_round_id"], "revision_touches": touches})
    report = {
        "schema": SCHEMA, "factory_round_id": start["factory_round_id"], "yield_status": yield_status,
        "start_snapshot_sha256": object_sha256(start),
        "starting_counts": start["counts"],
        "ending_counts": {"repository_revisions": len(end_revisions), "processed_revisions": len(repositories), "design_instances": len(designs), "design_families": len({row['family_id'] for row in designs}), "gold_families": len(end_gold)},
        "operational_delta": {"new_acquired_revisions": len(operational_new_revisions), "new_accepted_design_instances": len(new_design_rows), "new_design_families": len(new_families), "new_gold_families": len(new_gold)},
        "acquisition_cohort": {
            "new_acquired_revisions": len(cohort_revisions), "processed_terminal_revisions": processed_count,
            "pending_processing_revisions": len(cohort_revisions) - processed_count, "processing_coverage": round(coverage, 6),
            "terminal_revision_set_matches_cohort": terminal_set_matches,
            "processing_identity_source": processing_identity_source,
            "terminal_revision_keys_sha256": object_sha256(sorted(cohort_terminal_revisions)),
            "nonterminal_revision_keys": sorted(nonterminal_revisions),
            "new_rtl_revisions": sum(repos_by_revision[key].get("classification") not in {"NO_RTL", None} for key in cohort_terminal_revisions),
            "new_project_targets": len(new_targets), "new_candidate_tops": len({(row.get('repo_id'), row.get('project_key'), row.get('top_candidate')) for row in cohort_failures} | {(row.get('provenance', {}).get('repo_id'), row.get('identity', {}).get('project_key'), row.get('build', {}).get('top_module')) for row in cohort_designs}),
            "new_accepted_design_instances": len(cohort_designs), "new_design_families": len(cohort_new_families), "new_gold_families": len(cohort_new_gold),
            "duplicate_existing_family_design_instances": len(duplicate_family_designs),
            "repository_classification": dict(Counter(str(repos_by_revision[key].get("classification", "UNKNOWN")) for key in cohort_terminal_revisions)),
            "repository_terminal_state": dict(Counter(str(repos_by_revision[key].get("state", "UNKNOWN")) for key in cohort_terminal_revisions)),
            "repository_outcome_detail": dict(Counter(str(repos_by_revision[key].get("repository_outcome_detail", "LEGACY_UNCLASSIFIED")) for key in cohort_terminal_revisions)),
            "failure_taxonomy": dict(Counter(str(row.get("failure_type", "UNKNOWN")) for row in cohort_failures)),
        },
        "marginal_yield": {
            "denominator": "new_acquired_revisions", "status": yield_status,
            "design_families_per_revision": round(len(cohort_new_families) / max(1, len(cohort_revisions)), 6),
            "gold_families_per_revision": round(len(cohort_new_gold) / max(1, len(cohort_revisions)), 6),
            "large_xlarge_design_instance_share": round((resource["LARGE"] + resource["XLARGE"]) / max(1, len(cohort_designs)), 6),
            "complex_function_design_instance_share": round(sum(row.get("functional_ontology", {}).get("label") in complex_labels for row in cohort_designs) / max(1, len(cohort_designs)), 6),
        },
        "resource_cost": {"network_bytes": sum(network_bytes_by_revision.values()), "generic_synthesis_cpu_hours": round(sum(float(row.get("synthesis", {}).get("runtime_seconds") or 0.0) for row in cohort_designs) / 3600.0, 9), "cpu_hours_per_new_family": round(sum(float(row.get("synthesis", {}).get("runtime_seconds") or 0.0) for row in cohort_designs) / 3600.0 / max(1, len(cohort_new_families)), 9)},
        "provider_strategy_query_family": [dict(provider=key[0], strategy=key[1], query_family=key[2], **dict(counts)) for key, counts in sorted(attribution_rows.items())],
        "admission_anchor_yield": [
            dict(
                admission_anchor=anchor,
                **dict(counts),
                no_rtl_rate=round(counts["no_rtl_revisions"] / max(1, counts["processed_revisions"]), 6),
                design_instances_per_revision=round(counts["new_design_instances"] / max(1, counts["new_acquired_revisions"]), 6),
                design_families_per_revision=round(counts["new_design_families"] / max(1, counts["new_acquired_revisions"]), 6),
                gold_families_per_revision=round(counts["new_gold_families"] / max(1, counts["new_acquired_revisions"]), 6),
            )
            for anchor, counts in sorted(anchor_rows.items())
        ],
        "attribution": {"schema": "rtl_round_primary_attribution_v1", "primary_credit": "EARLIEST_DISCOVERY_EVENT", "family_credit": "LEXICOGRAPHICALLY_FIRST_NEW_COHORT_REVISION", "multi_touch_events_preserved": True, "multi_touch_revision_count": len(touches), "evidence_path": str(attribution_path)},
        "cohort_lock": ({"schema": cohort_lock["schema"], "path": str(lock_path), "sha256": file_sha256(lock_path), "revision_keys_sha256": cohort_lock.get("revision_keys_sha256"), "locked_at": cohort_lock.get("locked_at"), "target_new_acquired_revisions": cohort_lock.get("target_new_acquired_revisions"), "requested_revision_target": cohort_lock.get("requested_revision_target", cohort_lock.get("target_new_acquired_revisions")), "actual_cohort_size": cohort_lock.get("actual_cohort_size", cohort_lock.get("acquired_revision_count")), "early_close": cohort_lock.get("early_close", False), "early_close_reason": cohort_lock.get("early_close_reason")} if cohort_lock else None),
        "new_revision_keys": sorted(cohort_revisions), "new_design_ids": sorted(row["design_id"] for row in new_design_rows),
        "new_family_ids": sorted(new_families), "new_gold_family_ids": sorted(new_gold), "factory_stages": stages or [],
        "cohort_new_design_ids": sorted(str(row["design_id"]) for row in cohort_designs),
        "cohort_new_family_ids": sorted(cohort_new_families),
        "cohort_new_gold_family_ids": sorted(cohort_new_gold),
    }
    atomic(output, report)
    atomic(corpus / "quality/phase2/phase2_round_delta_summary.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("begin", "finalize", "backfill"))
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--acquired-count", type=int)
    parser.add_argument("--processing-run", type=Path)
    args = parser.parse_args()
    start_path = args.corpus_root / "quality/phase2/rounds" / args.round_id / "start.json"
    if args.action == "begin":
        result = capture(args.corpus_root, args.round_id)
    elif args.action == "backfill":
        if args.acquired_count is None or args.processing_run is None:
            parser.error("backfill requires --acquired-count and --processing-run")
        start = backfill_start(args.corpus_root, args.round_id, args.acquired_count, args.processing_run)
        result = finalize(args.corpus_root, start)
    else:
        result = finalize(args.corpus_root, json.loads(start_path.read_text(encoding="utf-8")))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
