#!/usr/bin/env python3
"""Validate, freeze, admit, rank, and promote Experiment 3 candidates."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = REPO / "docs/experiments/signoff/sky130hd_100mhz_fixed_task_repair_action_policy.json"
PROTOCOL_PATH = REPO / "experiments/experiment3_protocol.py"
SPEC = importlib.util.spec_from_file_location("experiment3_protocol", PROTOCOL_PATH)
PROTOCOL = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PROTOCOL)


REQUIRED_FIELDS = {
    "candidate_id",
    "candidate_version",
    "source_model",
    "failure_domain",
    "strategy",
    "rationale",
    "applicability",
    "config_edits",
    "action_policy_sha256",
}
ALLOWED_FIELDS = REQUIRED_FIELDS | {"candidate_hash"}
DEFAULT_FAILURE_DOMAINS = {"drc_edge_pin", "setup_timing"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def executable_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable behavior identity, excluding names and prose provenance."""
    return {
        "action_policy_sha256": candidate["action_policy_sha256"],
        "applicability": candidate["applicability"],
        "config_edits": candidate["config_edits"],
        "failure_domain": candidate["failure_domain"],
    }


def candidate_hash(candidate: dict[str, Any]) -> str:
    encoded = json.dumps(executable_payload(candidate), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _allowed_values(policy: dict[str, Any]) -> dict[str, set[str]]:
    allowed: dict[str, set[str]] = {}
    for key, values in (policy.get("allowed_string_knobs") or {}).items():
        allowed[key] = {str(value) for value in values}
    for key, bounds in (policy.get("allowed_numeric_knobs") or {}).items():
        minimum = bounds.get("minimum")
        maximum = bounds.get("maximum")
        if minimum != maximum:
            raise ValueError(f"Experiment 3 requires a finite enumerated value for {key}")
        allowed[key] = {str(minimum)}
    return allowed


def _policy_platform(policy: dict[str, Any]) -> str:
    return str(policy.get("platform") or "sky130hd")


def _policy_failure_domains(policy: dict[str, Any]) -> set[str]:
    return {
        str(value)
        for value in (policy.get("failure_domains") or DEFAULT_FAILURE_DOMAINS)
    }


def validate_candidate(candidate: dict[str, Any], policy_path: Path = DEFAULT_POLICY) -> list[str]:
    errors: list[str] = []
    unknown = set(candidate) - ALLOWED_FIELDS
    missing = REQUIRED_FIELDS - set(candidate)
    if unknown:
        errors.append(f"unknown fields: {sorted(unknown)}")
    if missing:
        errors.append(f"missing fields: {sorted(missing)}")
        return errors
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", str(candidate["candidate_id"])):
        errors.append("candidate_id is not a stable slug")
    if not isinstance(candidate["candidate_version"], int) or candidate["candidate_version"] < 1:
        errors.append("candidate_version must be a positive integer")
    policy = read_json(policy_path)
    if candidate["failure_domain"] not in _policy_failure_domains(policy):
        errors.append("unsupported failure_domain")
    applicability = candidate.get("applicability")
    expected_platform = _policy_platform(policy)
    if not isinstance(applicability, dict) or applicability.get("platform") != expected_platform:
        errors.append(f"applicability must bind platform={expected_platform}")
    elif not applicability.get("required_failure_signatures"):
        errors.append("applicability requires at least one failure signature")
    policy_hash = sha256_file(policy_path)
    if candidate["action_policy_sha256"] != policy_hash:
        errors.append("candidate action_policy_sha256 does not match the frozen policy")
    edits = candidate.get("config_edits")
    if not isinstance(edits, dict) or not edits or len(edits) > 6:
        errors.append("config_edits must contain 1..6 bounded edits")
    else:
        allowed = _allowed_values(policy)
        for key, value in edits.items():
            if key not in allowed:
                errors.append(f"config knob is not allowed: {key}")
            elif str(value) not in allowed[key]:
                errors.append(f"value is not allowed: {key}={value}")
    supplied_hash = candidate.get("candidate_hash")
    if supplied_hash is not None and supplied_hash != candidate_hash(candidate):
        errors.append("candidate_hash does not match the canonical payload")
    return errors


def freeze_candidate(candidate: dict[str, Any], policy_path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    frozen = dict(candidate)
    frozen.setdefault("action_policy_sha256", sha256_file(policy_path))
    frozen["candidate_hash"] = candidate_hash(frozen)
    errors = validate_candidate(frozen, policy_path)
    if errors:
        raise ValueError("; ".join(errors))
    return frozen


def _target_improved(row: dict[str, Any]) -> bool:
    if row.get("verdict") == "win" or row.get("strict_clean_after_repair") is True:
        return True
    domain = row.get("failure_domain")
    if domain in {"drc_edge_pin", "antenna_drc"}:
        return float(row.get("drc_delta") or 0) < 0
    if domain == "setup_timing":
        return float(row.get("wns_delta_ns") or 0) > 0
    return False


def admit_candidate(
    candidate: dict[str, Any],
    exploratory: list[dict[str, Any]],
    policy_path: Path = DEFAULT_POLICY,
) -> dict[str, Any]:
    frozen = freeze_candidate(candidate, policy_path)
    matching = [row for row in exploratory if row.get("candidate_hash") == frozen["candidate_hash"]]
    hard_regression = any(
        row.get("hard_regression") is True or row.get("clean_sentinel_regression") is True
        for row in matching
    )
    complete = [row for row in matching if row.get("infrastructure_complete") is True]
    positive = [row for row in complete if _target_improved(row)]
    admitted = bool(positive) and not hard_regression
    return {
        "candidate": frozen,
        "candidate_hash": frozen["candidate_hash"],
        "status": "candidate" if admitted else "rejected",
        "admitted": admitted,
        "complete_exploratory_trials": len(complete),
        "positive_exploratory_trials": len(positive),
        "hard_regression": hard_regression,
        "reason": (
            "positive_a_propose_without_hard_regression"
            if admitted
            else "hard_regression" if hard_regression else "no_complete_positive_a_propose_evidence"
        ),
    }


def deduplicate_candidates(
    proposals: list[dict[str, Any]],
    exploratory: list[dict[str, Any]],
    policy_path: Path = DEFAULT_POLICY,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proposal in proposals:
        frozen = freeze_candidate(proposal, policy_path)
        grouped[frozen["candidate_hash"]].append(frozen)
    records: list[dict[str, Any]] = []
    for fingerprint, group in sorted(grouped.items()):
        canonical = min(
            group,
            key=lambda row: (
                row["candidate_id"], row["candidate_version"], row["source_model"]
            ),
        )
        record = admit_candidate(canonical, exploratory, policy_path)
        record["proposal_sources"] = sorted({row["source_model"] for row in group})
        record["proposal_ids"] = sorted(
            {f"{row['source_model']}:{row['candidate_id']}:v{row['candidate_version']}" for row in group}
        )
        record["structurally_deduplicated_count"] = len(group)
        assert record["candidate_hash"] == fingerprint
        records.append(record)
    return records


def m1_order(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    admitted = [row for row in records if row.get("admitted") is True]
    return sorted(
        admitted,
        key=lambda row: (
            row["candidate"]["failure_domain"],
            row["candidate"]["candidate_id"],
            row["candidate"]["candidate_version"],
            row["candidate_hash"],
        ),
    )


def m2_order(records: list[dict[str, Any]], formal_a_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scores: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in formal_a_evidence:
        if row.get("split") not in {"a_propose", "a_validation"}:
            continue
        if row.get("provenance_complete") is not True:
            continue
        score = scores[str(row.get("candidate_hash") or "")]
        score["trials"] += 1
        if row.get("verdict") == "win":
            score["wins"] += 1
        elif row.get("verdict") == "loss":
            score["losses"] += 1
        score["strict_clean"] += float(row.get("strict_clean_after_repair") is True)
        score["wns_gain"] += max(0.0, float(row.get("wns_delta_ns") or 0.0))
        score["drc_reduction"] += max(0.0, -float(row.get("drc_delta") or 0.0))
        score["runtime"] += max(0.0, float(row.get("elapsed_seconds") or 0.0))

    def rank_key(record: dict[str, Any]) -> tuple[Any, ...]:
        score = scores[record["candidate_hash"]]
        utility = (
            1000.0 * score["wins"]
            - 1200.0 * score["losses"]
            + 100.0 * score["strict_clean"]
            + min(score["drc_reduction"], 100.0)
            + 10.0 * min(score["wns_gain"], 10.0)
            - 0.001 * score["runtime"]
        )
        return (
            record["candidate"]["failure_domain"],
            -utility,
            record["candidate"]["candidate_id"],
            record["candidate_hash"],
        )

    return sorted((row for row in records if row.get("admitted") is True), key=rank_key)


def promote(records: list[dict[str, Any]], formal_a_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for record in records:
        if record.get("admitted") is not True:
            continue
        evidence = [
            row for row in formal_a_evidence
            if row.get("candidate_hash") == record["candidate_hash"]
        ]
        if not evidence:
            verdict = {
                "candidate_hash": record["candidate_hash"],
                "independent_wins": 0,
                "independent_losses": 0,
                "validation_wins": 0,
                "clean_sentinel_regression": False,
                "operational_promoted": False,
                "strict_validation_supported": False,
                "status": "candidate",
            }
        else:
            verdict = PROTOCOL.judge_promotion(evidence)
        result.append({**record, "promotion": verdict})
    return result


def m3_order(
    records: list[dict[str, Any]],
    formal_a_evidence: list[dict[str, Any]],
    promotion_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    promoted = {
        row["candidate_hash"]
        for row in promotion_records
        if (row.get("promotion") or {}).get("operational_promoted") is True
    }
    return [row for row in m2_order(records, formal_a_evidence)
            if row["candidate_hash"] in promoted]


def command_freeze(args: argparse.Namespace) -> None:
    write_json(args.output, freeze_candidate(read_json(args.input), args.policy))


def command_build(args: argparse.Namespace) -> None:
    proposals = read_json(args.proposals)
    evidence = read_json(args.exploratory_evidence)
    formal = read_json(args.formal_a_evidence) if args.formal_a_evidence else []
    # A first build admits from exploration. A later build includes formal-A and
    # sentinel evidence so a discovered regression removes the candidate from
    # both M1/M2 as well as M3.
    records = deduplicate_candidates(proposals, [*evidence, *formal], args.policy)
    promotion_records = promote(records, formal)
    payload = {
        "schema_version": "experiment3-candidate-bank-1.0",
        "candidate_records": records,
        "m1_order": [row["candidate_hash"] for row in m1_order(records)],
        "m2_order": [row["candidate_hash"] for row in m2_order(records, formal)],
        "m3_order": [
            row["candidate_hash"]
            for row in m3_order(records, formal, promotion_records)
        ],
        "promotion_records": promotion_records,
    }
    write_json(args.output, payload)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--input", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    freeze.set_defaults(func=command_freeze)
    build = subparsers.add_parser("build-bank")
    build.add_argument("--proposals", type=Path, required=True)
    build.add_argument("--exploratory-evidence", type=Path, required=True)
    build.add_argument("--formal-a-evidence", type=Path)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    build.set_defaults(func=command_build)
    return result


def main() -> int:
    args = parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
