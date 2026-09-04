#!/usr/bin/env python3
"""Bind P13 anti-forgetting gates to immutable replay evidence files.

This command is a provenance binder, not a gate oracle.  The manifest must
explicitly state the result of the target replay, non-target regression audit,
held-out audit, and rollback verification; this script only verifies that each
statement names a distinct file with the declared SHA256 and emits a typed
``AntiForgettingWitness``.  An ineligible witness remains audit evidence and
cannot be consumed by the P13 shadow-update runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evolution import AntiForgettingWitness  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402


MANIFEST_VERSION = "p13-anti-forgetting-manifest-v1"
REPORT_VERSION = "p13-anti-forgetting-witness-report-v1"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GATES = (
    ("target_replay", "target_replay_receipt_id", "target_replay_passed"),
    ("non_target_regression", "non_target_regression_receipt_id",
     "non_target_regression_free"),
    ("heldout_audit", "heldout_audit_receipt_id", "heldout_audit_passed"),
)


class P13AntiForgettingError(ValueError):
    """The anti-forgetting manifest is malformed or unsafe."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(value: Mapping) -> str:
    return "sha256:" + hashlib.sha256(
        stable_dumps(dict(value)).encode()).hexdigest()


def _load_json(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13AntiForgettingError(f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13AntiForgettingError(f"{name} must be a JSON object")
    return payload


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise P13AntiForgettingError(f"{name} must be a non-empty string")
    return value.strip()


def _digest_text(value: object, name: str) -> str:
    value = _text(value, name)
    if _DIGEST_RE.fullmatch(value) is None:
        raise P13AntiForgettingError(f"{name} must be a sha256 digest")
    return value


def _strict_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise P13AntiForgettingError(f"{name} must be boolean")
    return value


def _evidence(
        raw: object, name: str, manifest_path: Path,
        forbidden_paths: set[Path],
        ) -> tuple[str, str, bool, dict]:
    if not isinstance(raw, Mapping):
        raise P13AntiForgettingError(f"{name} must be an object")
    receipt_id = _text(raw.get("receipt_id"), f"{name}.receipt_id")
    raw_path = _text(raw.get("path"), f"{name}.path")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve()
    if path in forbidden_paths:
        raise P13AntiForgettingError(
            f"{name}.path must be independent from the manifest and output")
    if not path.is_file():
        raise P13AntiForgettingError(f"{name}.path is not a file: {path}")
    expected = _digest_text(raw.get("sha256"), f"{name}.sha256")
    actual = _sha256(path)
    if expected != actual:
        raise P13AntiForgettingError(f"{name}.sha256 does not match {path}")
    status_name = "regression_free" if name == "non_target_regression" else (
        "verified" if name == "rollback" else "passed")
    status = _strict_bool(raw.get(status_name), f"{name}.{status_name}")
    details = {
        "receipt_id": receipt_id, "path": str(path), "sha256": actual,
        status_name: status,
    }
    return receipt_id, actual, status, details


def build_p13_anti_forgetting_witness(
        manifest: Path | str, *, output: Path | str) -> dict:
    """Bind four explicit anti-forgetting gate statements to files."""
    manifest_path = Path(manifest).expanduser().resolve()
    payload = _load_json(manifest_path, "P13 anti-forgetting manifest")
    if payload.get("version") != MANIFEST_VERSION:
        raise P13AntiForgettingError(
            "P13 anti-forgetting manifest version mismatch")
    campaign_id = _text(payload.get("campaign_id"), "campaign_id")
    case_id = _text(payload.get("case_id"), "case_id")
    output_path = Path(output).expanduser().resolve()
    forbidden_paths = {manifest_path, output_path}

    values: dict[str, object] = {}
    evidence: dict[str, dict] = {}
    seen_paths: set[str] = set()
    for name, witness_id, witness_status in _GATES:
        receipt_id, _digest_value, status, details = _evidence(
            payload.get(name), name, manifest_path, forbidden_paths)
        if details["path"] in seen_paths:
            raise P13AntiForgettingError(
                "anti-forgetting evidence paths must be distinct")
        seen_paths.add(details["path"])
        values[witness_id] = receipt_id
        values[witness_status] = status
        evidence[name] = details

    rollback = payload.get("rollback")
    if not isinstance(rollback, Mapping):
        raise P13AntiForgettingError("rollback must be an object")
    rollback_pointer = _text(rollback.get("pointer"), "rollback.pointer")
    rollback_id, rollback_digest, rollback_verified, rollback_details = _evidence(
        rollback, "rollback", manifest_path, forbidden_paths)
    if rollback_details["path"] in seen_paths:
        raise P13AntiForgettingError(
            "anti-forgetting evidence paths must be distinct")
    values.update({
        "rollback_pointer": rollback_pointer,
        "rollback_receipt_digest": rollback_digest,
        "rollback_verified": rollback_verified,
    })
    # ``rollback_id`` is intentionally checked and retained in the report;
    # AntiForgettingWitness names the pointer rather than a second ID field.
    rollback_details["receipt_id"] = rollback_id
    evidence["rollback"] = rollback_details

    witness = AntiForgettingWitness(
        **values,
        target_replay_digest=evidence["target_replay"]["sha256"],
        non_target_regression_digest=evidence["non_target_regression"]["sha256"],
        heldout_audit_digest=evidence["heldout_audit"]["sha256"],
        evidence_refs=tuple(sorted(item["path"] for item in evidence.values())),
    )
    witness_payload = {
        **witness.to_dict(), "receipt_digest": witness.receipt_digest,
    }
    report = {
        "version": REPORT_VERSION,
        "campaign_id": campaign_id,
        "case_id": case_id,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "manifest_digest": _digest(payload),
        "witness": witness_payload,
        "evidence": evidence,
        "eligible": witness.eligible,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    if output_path in {manifest_path, *[Path(item["path"]) for item in evidence.values()]}:
        raise P13AntiForgettingError(
            "anti-forgetting witness output must be separate from evidence")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_p13_anti_forgetting_witness(
            args.manifest, output=args.output)
    except (OSError, P13AntiForgettingError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "case_id": report["case_id"],
        "eligible": report["eligible"],
        "witness_digest": report["witness"]["receipt_digest"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
