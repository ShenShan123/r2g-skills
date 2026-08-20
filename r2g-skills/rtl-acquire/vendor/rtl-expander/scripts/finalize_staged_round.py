#!/usr/bin/env python3
"""Publish one immutable cohort from already validated revision staging artifacts.

This is the incremental finalization entrypoint.  It never re-runs repository
classification, frontend, elaboration, or synthesis.  The exact locked cohort
is joined to terminal processing-queue receipts, then only changed corpus
objects are committed to the ledger/index.  Compatibility manifests remain a
full, sequential materialized view and global split/publication validation is
retained as the correctness backstop.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from corpus_state import CorpusState, canonical, digest_bytes
from run_expansion_round import (
    FileLock,
    assign_families_and_splits,
    family_signatures,
    load_jsonl,
    split_hierarchy_top_member,
    split_project_member,
    split_source_members,
    state_canonical,
    state_digest,
    summarize,
    utc_now,
    validate_publish_invariants,
    write_jsonl,
    write_manifests,
    write_split_indexes,
)


SCHEMA = "rtl_incremental_finalization_v1"
CHANGE_SET_SCHEMA = "rtl_round_change_set_v1"
FINALIZATION_PLAN_SCHEMA = "rtl_finalization_preflight_plan_v1"


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(canonical(value))
    os.replace(temporary, path)


def load_cohort(path: Path) -> tuple[dict[str, Any], set[str]]:
    cohort = json.loads(path.read_text(encoding="utf-8"))
    keys = {str(value) for value in cohort.get("revision_keys", [])}
    expected = int(cohort.get("acquired_revision_count", cohort.get("cohort_size", -1)))
    if not keys or len(keys) != expected:
        raise RuntimeError("cohort lock count/key mismatch")
    return cohort, keys


def resolve_staged_revision_identity(
    expected_revision_key: str,
    queue_row: dict[str, Any],
    artifact_path: Path,
    artifact: dict[str, Any],
) -> str:
    """Resolve an optional artifact identity from authoritative locked context.

    The cohort key and processing-queue key are the revision authority.  The
    queue run key must bind both the artifact payload and its immutable
    run-keyed filename.  A legacy terminal artifact may omit its revision key
    when it has no DesignInstance payload; an asserted conflicting key is never
    repaired or ignored.
    """
    queue_revision_key = str(queue_row.get("repository_revision_key") or "")
    if not queue_revision_key or queue_revision_key != expected_revision_key:
        raise RuntimeError(
            f"locked/processing revision identity mismatch: {expected_revision_key}"
        )
    queue_run_key = str(queue_row.get("run_key") or "")
    artifact_run_key = str(artifact.get("run_key") or "")
    if not queue_run_key or not artifact_run_key:
        raise RuntimeError(f"missing staging run-key identity: {expected_revision_key}")
    if artifact_run_key != queue_run_key or artifact_path.stem != queue_run_key:
        raise RuntimeError(f"staging run-key mismatch: {expected_revision_key}")

    repository = artifact.get("repository")
    asserted_keys = {
        str(value)
        for value in [
            repository.get("repository_revision_key") if isinstance(repository, dict) else None,
            *[
                design.get("source", {}).get("repository_revision_key")
                for design in artifact.get("designs", [])
                if isinstance(design, dict)
            ],
        ]
        if value
    }
    if asserted_keys and asserted_keys != {expected_revision_key}:
        raise RuntimeError(f"staging revision identity mismatch: {expected_revision_key}")
    return expected_revision_key


def staged_payloads(corpus: Path, round_id: str, cohort_keys: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repositories, designs, _ = staged_payloads_with_identity(
        corpus, round_id, cohort_keys,
    )
    return repositories, designs


def staged_payloads_with_identity(
    corpus: Path, round_id: str, cohort_keys: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    database = corpus / "state/corpus.sqlite"
    if not database.is_file():
        raise RuntimeError("terminal processing queue index is missing")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM processing_queue WHERE round_id=? ORDER BY repository_revision_key",
            (round_id,),
        )]
    finally:
        connection.close()
    queue_keys = {str(row["repository_revision_key"]) for row in rows}
    terminal_keys = {
        str(row["repository_revision_key"])
        for row in rows if row.get("state") == "TERMINAL"
    }
    if queue_keys != cohort_keys or terminal_keys != cohort_keys:
        raise RuntimeError("terminal processing key set does not exactly match cohort")
    by_key = {str(row["repository_revision_key"]): row for row in rows}
    repositories: list[dict[str, Any]] = []
    designs: list[dict[str, Any]] = []
    bindings: list[tuple[str, str, str, str, int]] = []
    backfilled = 0
    for revision_key in sorted(cohort_keys):
        row = by_key.get(revision_key)
        if not row or row.get("state") != "TERMINAL":
            raise RuntimeError(f"missing terminal staging row: {revision_key}")
        artifact_path = Path(str(row.get("artifact_path") or ""))
        if not artifact_path.is_file():
            raise RuntimeError(f"missing staging artifact: {revision_key}")
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact_sha256 = str(row.get("artifact_sha256") or "").lower()
        artifact_size = row.get("artifact_size")
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256) or not isinstance(
            artifact_size, int
        ) or artifact_size < 0:
            raise RuntimeError(
                f"missing staging admission digest; explicit migration required: {revision_key}"
            )
        repository = artifact.get("repository")
        if not isinstance(repository, dict):
            raise RuntimeError(f"invalid staged repository payload: {revision_key}")
        resolved_key = resolve_staged_revision_identity(
            revision_key, row, artifact_path, artifact,
        )
        terminal = str(row.get("terminal_state") or "")
        if terminal != str(repository.get("state") or ""):
            raise RuntimeError(f"staging terminal-state mismatch: {revision_key}")
        repository = copy.deepcopy(repository)
        if not repository.get("repository_revision_key"):
            backfilled += 1
        repository.setdefault("repository_revision_key", resolved_key)
        repository.setdefault("stage_status", {})["PUBLISHED"] = "DONE"
        repositories.append(repository)
        designs.extend(copy.deepcopy(value) for value in artifact.get("designs", []) if isinstance(value, dict))
        bindings.append((
            revision_key, str(row["run_key"]), str(artifact_path.resolve()),
            artifact_sha256, artifact_size,
        ))
    identity = {
        "cohort_terminal_exact": True,
        "resolved_revision_count": len(bindings),
        "explicit_revision_key_count": len(bindings) - backfilled,
        "backfilled_revision_key_count": backfilled,
        "revision_keys_sha256": digest_bytes(canonical(sorted(cohort_keys))),
        "run_identity_bindings_sha256": digest_bytes(canonical(bindings)),
    }
    return repositories, designs, identity


def logical_membership_hash(designs: dict[str, dict[str, Any]]) -> str:
    rows = [
        (
            design_id,
            row.get("family_id"),
            row.get("split_group_id"),
            row.get("split"),
            row.get("quality", {}).get("training_tier"),
            row.get("release", {}).get("license_status"),
            row.get("release", {}).get("release_policy"),
            row.get("contamination", {}).get("benchmark_contaminated"),
        )
        for design_id, row in sorted(designs.items())
    ]
    return digest_bytes(canonical(rows))


def affected_identity_design_ids(
    corpus: Path,
    existing_designs: dict[str, dict[str, Any]],
    staged_designs: dict[str, dict[str, Any]],
    processed_repo_ids: set[str],
    split_reconciliation_plan: Path | None,
) -> set[str]:
    """Find the transitive Family/Split neighborhood touched by new evidence."""
    if split_reconciliation_plan is not None:
        plan = json.loads(split_reconciliation_plan.read_text(encoding="utf-8"))
        if plan.get("schema") == "rtl_split_profile_transition_v1":
            # A profile rollover changes split identity on every live design.
            return set(existing_designs) | set(staged_designs)
    signature_index = load_jsonl(
        corpus / "manifests/family_signature_index.jsonl", "signature"
    )
    membership_index = load_jsonl(
        corpus / "manifests/split_membership_index.jsonl", "member"
    )
    split_assignments = load_jsonl(
        corpus / "manifests/split_assignments.jsonl", "split_group_id"
    )
    family_to_designs: dict[str, set[str]] = {}
    for design_id, row in existing_designs.items():
        family_to_designs.setdefault(str(row.get("family_id")), set()).add(design_id)
    touched_designs = set(staged_designs)
    touched_families: set[str] = set()
    touched_groups: set[str] = set()
    for design_id, row in existing_designs.items():
        if str(row.get("provenance", {}).get("repo_id")) in processed_repo_ids:
            touched_designs.add(design_id)
            if row.get("family_id"):
                touched_families.add(str(row["family_id"]))
            if row.get("split_group_id"):
                touched_groups.add(str(row["split_group_id"]))
    for row in staged_designs.values():
        for _, signature in family_signatures(row):
            if signature in signature_index:
                touched_families.add(str(signature_index[signature]["family_id"]))

    changed = True
    while changed:
        before = (len(touched_designs), len(touched_families), len(touched_groups))
        for family_id in list(touched_families):
            touched_designs.update(family_to_designs.get(family_id, set()))
        records = [
            staged_designs.get(design_id) or existing_designs.get(design_id)
            for design_id in touched_designs
        ]
        members: set[str] = set()
        for row in records:
            if not row:
                continue
            if row.get("family_id"):
                touched_families.add(str(row["family_id"]))
                members.add(f"family:{row['family_id']}")
            members.update(split_source_members(row))
            members.add(split_project_member(row))
            members.add(split_hierarchy_top_member(row, row["build"]["top_module"]))
            members.update(
                split_hierarchy_top_member(row, str(dependency))
                for dependency in row.get("build", {}).get("dependency_modules", [])
            )
        touched_groups.update(
            str(membership_index[member]["split_group_id"])
            for member in members if member in membership_index
        )
        for group_id in list(touched_groups):
            touched_families.update(map(str, split_assignments.get(group_id, {}).get("family_ids", [])))
        after = (len(touched_designs), len(touched_families), len(touched_groups))
        changed = after != before
    return touched_designs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--cohort-lock", type=Path, required=True)
    parser.add_argument("--split-reconciliation-plan", type=Path)
    parser.add_argument("--change-set-output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--commit-plan", type=Path)
    parser.add_argument("--finalization-plan-output", type=Path)
    parser.add_argument("--split-seed", default="rtl-corpus-v1")
    parser.add_argument("--train-percent", type=int, default=90)
    parser.add_argument("--val-percent", type=int, default=5)
    parser.add_argument("--organization-aware-split", action="store_true")
    return parser.parse_args()


def recorded_cohort_digest(corpus: Path, round_id: str, cohort: dict[str, Any]) -> str:
    round_dir = corpus / "quality/phase2/rounds" / round_id
    receipt_path = round_dir / "cohort_lock.admission.json"
    controller_path = round_dir / "target_controller.json"
    controller: dict[str, Any] = {}
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("schema") != "rtl_immutable_artifact_admission_v1"
            or receipt.get("object_id") != "cohort_lock.json"
            or receipt.get("rehash_required") is not False
        ):
            raise RuntimeError("invalid cohort-lock admission receipt")
        recorded = str(receipt.get("sha256") or "").lower()
    elif controller_path.is_file():
        controller = json.loads(controller_path.read_text(encoding="utf-8"))
        recorded = str(controller.get("cohort_lock_sha256") or "").lower()
    else:
        recorded = ""
    if not re.fullmatch(r"[0-9a-f]{64}", recorded):
        raise RuntimeError("REHASH_REQUIRED: cohort lock lacks an admission digest")
    keys = sorted(map(str, cohort.get("revision_keys", [])))
    key_digest = hashlib.sha256(
        json.dumps(keys, separators=(",", ":")).encode()
    ).hexdigest()
    recorded_key_digest = controller.get("cohort_revision_keys_sha256") or cohort.get(
        "revision_keys_sha256"
    )
    if recorded_key_digest != key_digest:
        raise RuntimeError("recorded cohort key identity does not match membership")
    recorded_count = controller.get("cohort_revision_count", cohort.get("cohort_size", -1))
    if int(recorded_count) != len(keys):
        raise RuntimeError("recorded cohort size does not match membership")
    return recorded


def recorded_view_commitments(corpus: Path) -> dict[str, dict[str, Any]]:
    """Read admission receipts only; never rehash materialized-view bytes."""
    result: dict[str, dict[str, Any]] = {}
    for name in (
        "family_signature_index.jsonl", "split_membership_index.jsonl",
        "split_assignments.jsonl", "split_profiles.jsonl",
    ):
        view_path = corpus / "manifests" / name
        receipt_path = corpus / "manifests" / f"{name}.admission.json"
        if not view_path.is_file():
            result[name] = {"state": "NOT_PRESENT"}
            continue
        if not receipt_path.is_file():
            raise RuntimeError(
                f"REHASH_REQUIRED: materialized view lacks admission receipt: {name}"
            )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        digest = str(receipt.get("sha256") or "").lower()
        if (
            receipt.get("schema") != "rtl_materialized_view_admission_v1"
            or receipt.get("object_id") != name
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or int(receipt.get("size", -1)) < 0
            or receipt.get("rehash_required") is not False
        ):
            raise RuntimeError(f"invalid materialized-view admission digest: {name}")
        result[name] = {
            "state": "ADMITTED", "sha256": digest,
            "size": int(receipt["size"]), "receipt": str(receipt_path.resolve()),
        }
    return result


def readonly_corpus_state(
    corpus: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
    """Read current indexed truth without schema creation, recovery, or writes."""
    database = corpus / "state/corpus.sqlite"
    if not database.is_file():
        raise RuntimeError("finalization preflight requires an existing corpus index")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        designs = {
            str(row[0]): json.loads(row[1])
            for row in connection.execute(
                "SELECT design_id,payload_json FROM designs ORDER BY design_id"
            )
        }
        if not designs:
            raise RuntimeError("finalization preflight refuses an unpopulated corpus index")
        repositories = {
            str(row[0]): json.loads(row[1])
            for row in connection.execute(
                "SELECT repo_id,payload_json FROM repositories ORDER BY repo_id"
            )
        }
        repositories.update({
            str(row[0]): json.loads(row[1])
            for row in connection.execute(
                "SELECT alias_repo_id,payload_json FROM repository_aliases ORDER BY alias_repo_id"
            )
        })
        generation = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        connection.close()
    return designs, repositories, generation


def split_plan_commitment(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"present": False, "sha256": "NONE", "components": []}
    plan = json.loads(path.read_text(encoding="utf-8"))
    components = [
        {
            "component_id": str(row.get("component_id") or ""),
            "authorized_component_hash": str(row.get("authorized_component_hash") or ""),
            "canonical_component_members": sorted(map(str, row.get("canonical_component_members", []))),
            "input_splits": sorted(map(str, row.get("input_splits", []))),
            "target_split": str(row.get("target_split") or ""),
        }
        for row in plan.get("components", [])
    ]
    return {
        "present": True,
        "path": str(path.resolve()),
        "sha256": str(plan.get("plan_sha256") or ""),
        "schema": plan.get("schema"),
        "mode": plan.get("reconciliation_mode"),
        "plan_sha256": plan.get("plan_sha256"),
        "components": components,
        "components_pairwise_disjoint": plan.get(
            "reconciliation_plan_components_pairwise_disjoint"
        ) is True,
        "component_overlap_count": plan.get("component_overlap_count"),
        "component_boundary_identity_edges": plan.get("component_boundary_identity_edges"),
        "component_member_loss": plan.get("component_member_loss"),
    }


def plan_digest(plan: dict[str, Any]) -> str:
    material = copy.deepcopy(plan)
    material.pop("plan_sha256", None)
    material.pop("created_at", None)
    return digest_bytes(canonical(material))


def prepare_finalization(args: argparse.Namespace) -> dict[str, Any]:
    corpus = args.corpus_root.resolve()
    cohort, cohort_keys = load_cohort(args.cohort_lock)
    cohort_sha256 = recorded_cohort_digest(corpus, args.round_id, cohort)
    staged_repositories, staged_designs, identity = staged_payloads_with_identity(
        corpus, args.round_id, cohort_keys,
    )
    options = SimpleNamespace(
        split_seed=args.split_seed,
        train_percent=args.train_percent,
        val_percent=args.val_percent,
        organization_aware_split=args.organization_aware_split,
        split_reconciliation_plan=args.split_reconciliation_plan,
    )
    base_designs, base_repositories, generation_before = readonly_corpus_state(corpus)
    prior_hashes = {
        design_id: state_digest(state_canonical(row))
        for design_id, row in base_designs.items()
    }
    processed_repo_ids = {str(row["repo_id"]) for row in staged_repositories}
    staged_by_id = {str(row["design_id"]): row for row in staged_designs}
    retired_design_ids = sorted(
        design_id for design_id, row in base_designs.items()
        if str(row.get("provenance", {}).get("repo_id")) in processed_repo_ids
        and design_id not in staged_by_id
    )
    for design_id in retired_design_ids:
        base_designs.pop(design_id, None)
    affected_identity_ids = affected_identity_design_ids(
        corpus, base_designs, staged_by_id, processed_repo_ids,
        args.split_reconciliation_plan,
    )
    base_designs.update(staged_by_id)
    base_repositories.update({str(row["repo_id"]): row for row in staged_repositories})
    identity_designs = {
        design_id: base_designs[design_id]
        for design_id in affected_identity_ids if design_id in base_designs
    }
    identity_designs.update(staged_by_id)
    split_state = assign_families_and_splits(
        identity_designs, corpus, options, publish_indexes=False,
    )
    base_designs.update(identity_designs)
    validate_publish_invariants(corpus, base_designs, split_state)
    changed_designs = [
        row for design_id, row in sorted(base_designs.items())
        if prior_hashes.get(design_id) != state_digest(state_canonical(row))
    ]
    affected_families = sorted({
        str(row.get("family_id")) for row in changed_designs if row.get("family_id")
    })
    affected_split_groups = sorted({
        str(row.get("split_group_id")) for row in changed_designs if row.get("split_group_id")
    })
    preview = {
        "schema": CHANGE_SET_SCHEMA,
        "finalization_schema": SCHEMA,
        "round_id": args.round_id,
        "cohort_lock_sha256": cohort_sha256,
        "cohort_size": len(cohort_keys),
        "ledger_generation_before": generation_before,
        "changed_design_ids": sorted(str(row["design_id"]) for row in changed_designs),
        "retired_design_ids": retired_design_ids,
        "affected_family_ids": affected_families,
        "affected_split_group_ids": affected_split_groups,
        "identity_design_ids_evaluated": sorted(identity_designs),
        "repository_revision_keys": sorted(cohort_keys),
        "logical_membership_sha256": logical_membership_hash(base_designs),
    }
    plan = {
        "schema": FINALIZATION_PLAN_SCHEMA,
        "state": "FINALIZATION_PLAN_READY",
        "round_id": args.round_id,
        "cohort": {
            "path": str(args.cohort_lock.resolve()),
            "sha256": cohort_sha256,
            "size": len(cohort_keys),
            "revision_keys_sha256": identity["revision_keys_sha256"],
        },
        "terminal_identity": identity,
        "split_authorization": split_plan_commitment(args.split_reconciliation_plan),
        "materialized_identity_inputs": recorded_view_commitments(corpus),
        "corpus_generation": generation_before,
        "validated_invariants": {
            "cohort_terminal_exact": True,
            "revision_identity_resolved": True,
            "explicit_identity_conflicts_zero": True,
            "family_split_publish_invariants_valid": True,
            "component_member_loss_zero": True,
            "lineage_cycles_zero": True,
            "nonunique_canonical_targets_zero": True,
        },
        "round_change_set_preview": preview,
        "created_at": utc_now(),
    }
    plan["plan_sha256"] = plan_digest(plan)
    return {
        "plan": plan, "cohort_keys": cohort_keys,
        "staged_repositories": staged_repositories,
        "staged_designs": staged_designs, "changed_designs": changed_designs,
        "retired_design_ids": retired_design_ids, "base_designs": base_designs,
        "base_repositories": base_repositories, "split_state": split_state,
    }


def load_verified_preflight(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("schema") != FINALIZATION_PLAN_SCHEMA:
        raise RuntimeError("unsupported finalization preflight plan schema")
    if plan.get("state") != "FINALIZATION_PLAN_READY":
        raise RuntimeError("finalization preflight plan is not ready")
    if plan.get("plan_sha256") != plan_digest(plan):
        raise RuntimeError("finalization preflight plan hash mismatch")
    return plan


def main() -> int:
    args = parse_args()
    corpus = args.corpus_root.resolve()
    prepared = prepare_finalization(args)
    plan = prepared["plan"]
    plan_output = args.finalization_plan_output or (
        corpus / "quality/phase2/rounds" / args.round_id / "finalization_plan.json"
    )
    if args.preflight_only:
        atomic_json(plan_output, plan)
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if args.commit_plan:
        authorized = load_verified_preflight(args.commit_plan)
        if authorized.get("plan_sha256") != plan.get("plan_sha256"):
            raise RuntimeError("finalization inputs changed after preflight")
    else:
        # Preserve the direct CLI entrypoint while still making its commit
        # authorization explicit and auditable.
        atomic_json(plan_output, plan)

    cohort_keys = prepared["cohort_keys"]
    staged_repositories = prepared["staged_repositories"]
    staged_designs = prepared["staged_designs"]
    changed_designs = prepared["changed_designs"]
    retired_design_ids = prepared["retired_design_ids"]
    base_designs = prepared["base_designs"]
    base_repositories = prepared["base_repositories"]
    split_state = prepared["split_state"]
    preview = plan["round_change_set_preview"]
    generation_before = int(preview["ledger_generation_before"])

    with FileLock(corpus / "locks/manifest.lock", blocking=True):
        with CorpusState(corpus) as state:
            generation_now = int(state.connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0])
            if generation_now != generation_before:
                raise RuntimeError("corpus generation changed during incremental finalization")
            changes = state.apply_incremental(
                repositories=staged_repositories,
                designs=changed_designs,
                retired_design_ids=retired_design_ids,
                retirement_reason=f"ROUND_REPLACED:{args.round_id}",
            )
        write_split_indexes(corpus, split_state)
        write_manifests(corpus, base_designs)

    change_set = {
        **preview,
        "finalization_plan_sha256": plan["plan_sha256"],
        "finalization_plan_path": str((args.commit_plan or plan_output).resolve()),
        "changes": changes,
        "created_at": utc_now(),
    }
    output = args.change_set_output or (
        corpus / "quality/phase2/rounds" / args.round_id / "round_change_set.json"
    )
    atomic_json(output, change_set)
    summary = summarize(
        corpus, base_designs, base_repositories,
        f"incremental-{args.round_id}-{utc_now().replace(':', '')}",
    )
    print(json.dumps({
        "schema": SCHEMA,
        "round_id": args.round_id,
        "cohort_size": len(cohort_keys),
        "staged_designs": len(staged_designs),
        "changed_designs": len(changed_designs),
        "retired_designs": len(retired_design_ids),
        "affected_families": len(preview["affected_family_ids"]),
        "affected_split_groups": len(preview["affected_split_group_ids"]),
        "finalization_plan_sha256": plan["plan_sha256"],
        "change_set": str(output),
        "summary": summary,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
