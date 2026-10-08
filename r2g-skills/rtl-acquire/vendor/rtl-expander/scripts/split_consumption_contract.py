#!/usr/bin/env python3
"""Explicit lifecycle contract for campaign-internal split profiles."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


CONTRACT_SCHEMA = "rtl_split_profile_consumption_contract_v1"
STATE_SCHEMA = "rtl_split_profile_consumption_state_v1"
INTERNAL = "CAMPAIGN_INTERNAL"
FINAL_FROZEN = "CAMPAIGN_FINAL_FROZEN"
BLOCKING_STATES = {"EXTERNALLY_PINNED", "CONSUMED_BY_TRAINING", "CONSUMED_BY_EVALUATION"}


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(canonical(value))
    os.replace(temporary, path)


def contract_paths(corpus: Path, objective_id: str) -> tuple[Path, Path]:
    root = corpus / "state/controllers" / objective_id
    return root / "split_profile_consumption_contract.json", root / "split_profile_consumption_state.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def current_profile(corpus: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profiles = read_jsonl(corpus / "manifests/split_profiles.jsonl")
    current = [row for row in profiles if row.get("status") == "CURRENT"]
    if len(current) != 1:
        raise RuntimeError("consumption contract requires exactly one CURRENT split profile")
    return current[0], profiles


def controller_identity(controller: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": controller.get("schema"),
        "objective_id": controller.get("objective_id"),
        "created_at": controller.get("created_at"),
        "target": controller.get("target"),
        "primary_metric": controller.get("primary_metric"),
        "hard_completion_target": controller.get("hard_completion_target"),
        "quality_and_completion_gates": controller.get("quality_and_completion_gates"),
    }


def logical_index_hash(corpus: Path) -> str:
    database = corpus / "state/corpus.sqlite"
    connection = sqlite3.connect(database)
    try:
        rows = [tuple(row) for row in connection.execute(
            "SELECT design_id,content_sha256,family_id,split_group_id FROM designs ORDER BY design_id"
        )]
    finally:
        connection.close()
    return digest(rows)


def profile_descends_from(
    profiles: list[dict[str, Any]], current_profile_id: str, initial_profile_id: str,
) -> bool:
    index = {str(row.get("profile_id")): row for row in profiles}
    seen: set[str] = set()
    cursor = current_profile_id
    while cursor and cursor not in seen:
        if cursor == initial_profile_id:
            return True
        seen.add(cursor)
        cursor = str(index.get(cursor, {}).get("supersedes") or "")
    return False


def contract_payload_hash(contract: dict[str, Any]) -> str:
    return digest({key: value for key, value in contract.items() if key != "contract_sha256"})


def state_payload_hash(state: dict[str, Any]) -> str:
    return digest({key: value for key, value in state.items() if key != "state_sha256"})


def create_contract(corpus: Path, objective_id: str, authorizing_record: str) -> dict[str, Any]:
    contract_path, state_path = contract_paths(corpus, objective_id)
    if contract_path.exists() or state_path.exists():
        raise FileExistsError("split-profile consumption contract/state already exists")
    controller_path = corpus / "state/controllers" / objective_id / "controller.json"
    controller = json.loads(controller_path.read_text(encoding="utf-8"))
    identity = controller_identity(controller)
    if identity.get("objective_id") != objective_id:
        raise RuntimeError("campaign controller identity mismatch")
    profile, _ = current_profile(corpus)
    profile_manifest = corpus / "manifests/split_profiles.jsonl"
    latest_release = corpus / "snapshots/latest_release.json"
    baseline = {
        "split_profile_id": profile["profile_id"],
        "split_schema": profile["split_schema"],
        "split_epoch": profile["split_epoch"],
        "split_profile_record_sha256": digest(profile),
        "split_profile_manifest_sha256": digest_file(profile_manifest),
        "campaign_controller_identity": identity,
        "campaign_controller_identity_sha256": digest(identity),
        "corpus_logical_index_sha256": logical_index_hash(corpus),
        "ledger_hashes": {
            name: digest_file(corpus / "ledger" / name)
            for name in (
                "repository_events.jsonl", "design_events.jsonl", "family_events.jsonl",
                "split_events.jsonl", "license_events.jsonl", "quality_events.jsonl",
            )
        },
        "latest_release_identity_sha256": digest_file(latest_release),
        "latest_release_sha256": (
            json.loads(latest_release.read_text(encoding="utf-8")).get("release_sha256")
            if latest_release.is_file() else "MISSING"
        ),
    }
    contract = {
        "schema": CONTRACT_SCHEMA,
        "campaign_id": objective_id,
        "objective_id": objective_id,
        "consumption_state": INTERNAL,
        "consumption_scope": INTERNAL,
        "external_training_allowed": False,
        "external_formal_evaluation_allowed": False,
        "external_training_or_evaluation_prohibited": True,
        "effective_until": {
            "formal_design_families_at_least": int(controller["target"]),
            "final_campaign_snapshot_status": "CERTIFIED",
            "condition": "ALL",
        },
        "authorization": {
            "authority": "USER_EXPLICIT_CONFIRMATION",
            "record": authorizing_record,
            "assertion": "NO_EVIDENCE_OF_EXTERNAL_TRAINING_OR_FORMAL_EVALUATION_CONSUMPTION",
        },
        "effective_from_identity": baseline,
        "created_at": utc_now(),
    }
    contract["contract_sha256"] = contract_payload_hash(contract)
    state = {
        "schema": STATE_SCHEMA,
        "campaign_id": objective_id,
        "contract_sha256": contract["contract_sha256"],
        "consumption_state": INTERNAL,
        "external_training_allowed": False,
        "external_formal_evaluation_allowed": False,
        "updated_at": utc_now(),
    }
    state["state_sha256"] = state_payload_hash(state)
    atomic_json(contract_path, contract)
    atomic_json(state_path, state)
    return contract


def load_and_validate(
    corpus: Path, objective_id: str, *, allowed_states: set[str] | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    contract_path, state_path = contract_paths(corpus, objective_id)
    if not contract_path.is_file():
        raise RuntimeError("automatic test-profile rollover lacks a campaign consumption contract")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    if contract.get("schema") != CONTRACT_SCHEMA or contract.get("contract_sha256") != contract_payload_hash(contract):
        raise RuntimeError("campaign consumption contract hash/schema is invalid")
    if state.get("schema") != STATE_SCHEMA or state.get("state_sha256") != state_payload_hash(state):
        raise RuntimeError("campaign consumption state hash/schema is invalid")
    if contract.get("campaign_id") != objective_id or state.get("campaign_id") != objective_id:
        raise RuntimeError("campaign consumption identity mismatch")
    if state.get("contract_sha256") != contract.get("contract_sha256"):
        raise RuntimeError("campaign consumption state is not bound to the contract")
    if contract.get("external_training_allowed") is not False or contract.get("external_formal_evaluation_allowed") is not False:
        raise RuntimeError("campaign contract does not prohibit external consumption")
    consumption_state = str(state.get("consumption_state"))
    if allowed_states is not None and consumption_state not in allowed_states:
        raise RuntimeError(f"split profile is not eligible in consumption state {consumption_state}")
    controller = json.loads(
        (corpus / "state/controllers" / objective_id / "controller.json").read_text(encoding="utf-8")
    )
    baseline = contract.get("effective_from_identity", {})
    if digest(controller_identity(controller)) != baseline.get("campaign_controller_identity_sha256"):
        raise RuntimeError("campaign controller identity no longer matches the contract")
    profile, profiles = current_profile(corpus)
    if not profile_descends_from(
        profiles, str(profile.get("profile_id")), str(baseline.get("split_profile_id")),
    ):
        raise RuntimeError("current split profile is outside the authorized campaign lineage")
    return contract_path, contract, state


def transition_state(
    corpus: Path, objective_id: str, new_state: str, evidence: dict[str, Any],
) -> dict[str, Any]:
    contract_path, contract, old = load_and_validate(
        corpus, objective_id, allowed_states={INTERNAL, FINAL_FROZEN, *BLOCKING_STATES},
    )
    allowed = {
        INTERNAL: {FINAL_FROZEN, *BLOCKING_STATES},
        FINAL_FROZEN: BLOCKING_STATES,
        "EXTERNALLY_PINNED": {"CONSUMED_BY_TRAINING", "CONSUMED_BY_EVALUATION"},
        "CONSUMED_BY_TRAINING": set(),
        "CONSUMED_BY_EVALUATION": set(),
    }
    prior = str(old["consumption_state"])
    if new_state != prior and new_state not in allowed.get(prior, set()):
        raise RuntimeError(f"invalid consumption transition {prior}->{new_state}")
    _, state_path = contract_paths(corpus, objective_id)
    profile, _ = current_profile(corpus)
    state = {
        "schema": STATE_SCHEMA,
        "campaign_id": objective_id,
        "contract_sha256": contract["contract_sha256"],
        "consumption_state": new_state,
        "external_training_allowed": new_state == FINAL_FROZEN,
        "external_formal_evaluation_allowed": new_state == FINAL_FROZEN,
        "previous_state_sha256": old["state_sha256"],
        "active_split_profile_id": profile["profile_id"],
        "evidence": evidence,
        "updated_at": utc_now(),
    }
    state["state_sha256"] = state_payload_hash(state)
    atomic_json(state_path, state)
    return state


def snapshot_metadata(corpus: Path) -> dict[str, Any]:
    controller_roots = sorted((corpus / "state/controllers").glob("*/split_profile_consumption_contract.json"))
    if not controller_roots:
        return {
            "consumption_scope": "UNDECLARED",
            "external_training_eligible": False,
            "external_evaluation_eligible": False,
        }
    if len(controller_roots) != 1:
        raise RuntimeError("multiple active split-profile consumption contracts")
    objective_id = controller_roots[0].parent.name
    path, contract, state = load_and_validate(
        corpus, objective_id, allowed_states={INTERNAL, FINAL_FROZEN, *BLOCKING_STATES},
    )
    frozen = state["consumption_state"] == FINAL_FROZEN
    return {
        "campaign_id": objective_id,
        "consumption_contract_sha256": contract["contract_sha256"],
        "consumption_contract_path": str(path.relative_to(corpus)),
        "consumption_state": state["consumption_state"],
        "consumption_scope": INTERNAL if not frozen else FINAL_FROZEN,
        "external_training_eligible": frozen,
        "external_evaluation_eligible": frozen,
    }


def materialize_snapshot_registry(corpus: Path, objective_id: str) -> dict[str, Any]:
    _, contract, state = load_and_validate(
        corpus, objective_id, allowed_states={INTERNAL, FINAL_FROZEN, *BLOCKING_STATES},
    )
    entries = []
    for root in sorted((corpus / "snapshots").glob(f"p2f_*_{objective_id}_batch*-final")):
        identity_path = root / "release_identity.json"
        completion_path = root / "completion.json"
        if not identity_path.is_file() or not completion_path.is_file():
            continue
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        entries.append({
            "snapshot_id": root.name,
            "release_sha256": identity.get("release_sha256"),
            "split_profile_id": identity.get("split_profile_id"),
            "certification": completion.get("status"),
            "consumption_scope": INTERNAL,
            "external_training_eligible": False,
            "external_evaluation_eligible": False,
        })
    registry = {
        "schema": "rtl_campaign_snapshot_consumption_registry_v1",
        "campaign_id": objective_id,
        "contract_sha256": contract["contract_sha256"],
        "consumption_state_at_materialization": state["consumption_state"],
        "snapshots": entries,
        "generated_at": utc_now(),
    }
    registry["registry_sha256"] = digest({
        key: value for key, value in registry.items() if key != "registry_sha256"
    })
    path = corpus / "state/controllers" / objective_id / "snapshot_consumption_registry.json"
    atomic_json(path, registry)
    return registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--objective-id", required=True)
    parser.add_argument("--create-campaign-internal", action="store_true")
    parser.add_argument("--authorizing-record")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--refresh-snapshot-registry", action="store_true")
    args = parser.parse_args()
    if args.create_campaign_internal:
        if not args.authorizing_record:
            raise SystemExit("--authorizing-record is required")
        result = create_contract(args.corpus_root, args.objective_id, args.authorizing_record)
    elif args.validate:
        _, contract, state = load_and_validate(args.corpus_root, args.objective_id)
        result = {"contract": contract, "state": state}
    elif args.refresh_snapshot_registry:
        result = materialize_snapshot_registry(args.corpus_root, args.objective_id)
    else:
        raise SystemExit("choose --create-campaign-internal or --validate")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
