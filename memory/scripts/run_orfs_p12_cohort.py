#!/usr/bin/env python3
"""Run a frozen, source-disjoint ORFS P12 cohort from a JSON manifest.

The manifest is the only campaign input.  Every memory arm is loaded from a
serialized ``StructuredRepairCandidate`` (or explicitly set to ``null``), and
the existing ORFS oracle/cohort harness performs the real four-arm execution.
This command does not open SQLite, infer NO_SKILL reasons, or import any result
into canonical memory, lifecycle authority, or production runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation import P12_ARMS, OrfsCandidateOracle, execute_orfs_paired_cohort  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.retrieval.structured_candidate import StructuredRepairCandidate  # noqa: E402


MANIFEST_VERSION = "p12-orfs-cohort-manifest-v1"
REPORT_VERSION = "p12-orfs-cohort-run-report-v1"
_MEMORY_ARMS = frozenset(P12_ARMS[1:])


class P12OrfsRunError(ValueError):
    """A P12 manifest or candidate binding is malformed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(payload)).encode()).hexdigest()


def _load_json(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P12OrfsRunError(f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P12OrfsRunError(f"{name} must be a JSON object")
    return payload


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise P12OrfsRunError(f"{name} must be a non-empty string")
    return value.strip()


def _digest_pin(value: object, name: str) -> str:
    value = _text(value, name)
    if not value.startswith("sha256:") or len(value) <= len("sha256:"):
        raise P12OrfsRunError(f"{name} must be a sha256 digest")
    return value


def _manifest(path: Path) -> tuple[dict, list[dict], int, int]:
    payload = _load_json(path, "P12 manifest")
    if payload.get("version") != MANIFEST_VERSION:
        raise P12OrfsRunError("P12 manifest version mismatch")
    campaign_id = _text(payload.get("campaign_id"), "campaign_id")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)) or not raw_cases:
        raise P12OrfsRunError("P12 manifest cases must be a non-empty sequence")
    budget = payload.get("candidate_budget", 3)
    if type(budget) is not int or not 1 <= budget <= 3:
        raise P12OrfsRunError("P12 manifest candidate_budget must be between one and three")
    min_lineages = payload.get("min_lineages", 2)
    if type(min_lineages) is not int or min_lineages < 1:
        raise P12OrfsRunError("P12 manifest min_lineages must be positive")
    platform_digest = _digest_pin(payload.get("platform_digest"), "platform_digest")
    pdk_digest = _digest_pin(payload.get("pdk_digest"), "pdk_digest")
    toolchain_digest = payload.get("toolchain_digest")
    oracle_digest = payload.get("oracle_digest")
    if toolchain_digest is not None:
        toolchain_digest = _digest_pin(toolchain_digest, "toolchain_digest")
    if oracle_digest is not None:
        oracle_digest = _digest_pin(oracle_digest, "oracle_digest")
    cases: list[dict] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, Mapping):
            raise P12OrfsRunError("each P12 case must be an object")
        case = dict(raw)
        case_id = _text(case.get("case_id"), "case_id")
        if case_id in seen:
            raise P12OrfsRunError("P12 case IDs must be unique")
        seen.add(case_id)
        for key in ("project_dir", "source_digest", "source_inputs", "platform_digest",
                    "pdk_digest", "toolchain_digest", "oracle_digest"):
            if key not in case:
                raise P12OrfsRunError(f"P12 case {case_id} is missing {key}")
        if _digest_pin(case["platform_digest"], f"{case_id}.platform_digest") != platform_digest:
            raise P12OrfsRunError(f"P12 case {case_id} platform digest drifts from manifest")
        if toolchain_digest is not None and _digest_pin(
                case["toolchain_digest"], f"{case_id}.toolchain_digest") != toolchain_digest:
            raise P12OrfsRunError(f"P12 case {case_id} toolchain digest drifts from manifest")
        if oracle_digest is not None and _digest_pin(
                case["oracle_digest"], f"{case_id}.oracle_digest") != oracle_digest:
            raise P12OrfsRunError(f"P12 case {case_id} oracle digest drifts from manifest")
        case["case_id"] = case_id
        cases.append(case)
    # The digest is over the immutable manifest payload, not a self-referential
    # field.  It is recorded in the cohort receipt and report for replay.
    payload_digest = _digest(payload)
    payload["campaign_id"] = campaign_id
    payload["platform_digest"] = platform_digest
    payload["pdk_digest"] = pdk_digest
    return payload, cases, budget, min_lineages


def _candidate_map(case: Mapping, manifest_path: Path) -> tuple[dict, dict[str, dict]]:
    case_id = _text(case.get("case_id"), "case_id")
    raw = case.get("candidate_paths")
    if not isinstance(raw, Mapping) or set(raw) != set(P12_ARMS):
        raise P12OrfsRunError(
            f"P12 case {case_id} candidate_paths must cover exactly all four arms")
    candidates: dict[str, StructuredRepairCandidate | None] = {}
    refs: dict[str, dict] = {}
    for arm in P12_ARMS:
        value = raw[arm]
        if value is None:
            if arm == "ALWAYS_MEMORY":
                raise P12OrfsRunError(
                    f"P12 case {case_id} ALWAYS_MEMORY requires a candidate")
            candidates[arm] = None
            continue
        path = Path(_text(value, f"{case_id}.{arm}.candidate_path")).expanduser()
        if not path.is_absolute():
            path = manifest_path.parent / path
        path = path.resolve()
        if not path.is_file():
            raise P12OrfsRunError(f"P12 candidate is not a file: {path}")
        try:
            candidate = StructuredRepairCandidate.from_dict(
                _load_json(path, f"{case_id}.{arm} candidate"))
        except (TypeError, ValueError) as exc:
            raise P12OrfsRunError(
                f"P12 candidate for {case_id}/{arm} is invalid: {exc}") from exc
        candidates[arm] = candidate
        refs[arm] = {"path": str(path), "sha256": _sha256(path),
                     "candidate_id": candidate.candidate_id,
                     "candidate_digest": candidate.candidate_digest}
    return candidates, refs


def run_p12_orfs_cohort(manifest: Path | str, *, output: Path | str,
                        timeout: int | None = None) -> dict:
    """Execute the exact four-arm P12 cohort described by ``manifest``."""
    manifest_path = Path(manifest).expanduser().resolve()
    payload, cases, budget, min_lineages = _manifest(manifest_path)
    campaign_id = _text(payload.get("campaign_id"), "campaign_id")
    if timeout is not None:
        if type(timeout) is not int or timeout < 1:
            raise P12OrfsRunError("timeout must be a positive integer")
    arm_candidates: dict[str, dict] = {}
    candidate_refs: dict[str, dict] = {}
    for case in cases:
        case_id = case["case_id"]
        candidates, refs = _candidate_map(case, manifest_path)
        arm_candidates[case_id] = candidates
        candidate_refs[case_id] = refs
    # ORFS scripts consume ORFS_TIMEOUT/ORFS_MAX_CPUS through their existing
    # environment contract.  This CLI deliberately does not reinterpret it.
    if timeout is not None:
        import os
        os.environ["ORFS_TIMEOUT"] = str(timeout)
    manifest_digest = _digest(payload)
    try:
        cohort = execute_orfs_paired_cohort(
            cases, arm_candidates, campaign_id=campaign_id,
            campaign_manifest_digest=manifest_digest,
            platform_digest=payload["platform_digest"],
            pdk_digest=payload["pdk_digest"],
            oracle=OrfsCandidateOracle(), budget=budget,
            toolchain_digest=payload.get("toolchain_digest"),
            oracle_digest=payload.get("oracle_digest"),
            min_lineages=min_lineages)
    except (TypeError, ValueError, OSError) as exc:
        raise P12OrfsRunError(str(exc)) from exc
    receipt = {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest}
    # Keep the canonical cohort receipt at the top level so this output can be
    # passed directly to ``build_p13_shadow_trigger_report.py``.  The nested
    # copy is retained as an explicit report boundary for callers that consume
    # runner metadata separately.
    report = {
        **receipt,
        "report_version": REPORT_VERSION,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "manifest_digest": manifest_digest,
        "candidate_refs": candidate_refs,
        "cohort_receipt": receipt,
        "cohort_receipt_digest": cohort.receipt_digest,
        "outcome_counts": cohort.outcome_counts,
        "lineage_count": cohort.lineage_count,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
    }
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int)
    args = parser.parse_args(argv)
    try:
        report = run_p12_orfs_cohort(args.manifest, output=args.output,
                                     timeout=args.timeout)
    except (OSError, P12OrfsRunError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "cohort_receipt_digest": report["cohort_receipt_digest"],
        "lineage_count": report["lineage_count"],
        "outcome_counts": report["outcome_counts"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
