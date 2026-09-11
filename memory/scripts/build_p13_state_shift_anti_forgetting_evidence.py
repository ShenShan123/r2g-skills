#!/usr/bin/env python3
"""Derive P13 StateShift anti-forgetting evidence from frozen inputs.

The target replay is supplied by the support-expansion report.  This command
independently verifies that the child preserves the parent claim and its real
training executions, still abstains on a preregistered held-out source, and
that the frozen source SQLite can be copied/rolled back without mutation.  It
emits three distinct oracle reports plus a manifest for the generic
``build_p13_anti_forgetting_witness.py`` binder.  It performs no EDA and no
canonical, lifecycle, or production write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evolution import (  # noqa: E402
    StateShiftSupportExpansionReceipt,
    build_isolated_rollback_receipt,
)
from tehm.evolution.verification import require_verified_transition  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.knowledge import MechanismKnowledge  # noqa: E402
from tehm.state import SupportEnvelope, evaluate_state_shift  # noqa: E402
from tehm.verified_execution import scoped_learning_replay  # noqa: E402


REPORT_VERSION = "p13-state-shift-anti-forgetting-evidence-v1"
HELDOUT_VERSION = "p13-state-shift-heldout-registration-v1"
MANIFEST_VERSION = "p13-anti-forgetting-manifest-v1"


class P13StateShiftAntiForgettingError(ValueError):
    """Frozen evidence cannot establish the four anti-forgetting gates."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _load(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13StateShiftAntiForgettingError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13StateShiftAntiForgettingError(f"{name} must be an object")
    return payload


def _content_digest(payload: Mapping, field: str, name: str) -> str:
    supplied = payload.get(field)
    replay = dict(payload)
    replay.pop(field, None)
    if type(supplied) is not str or supplied != _digest(replay):
        raise P13StateShiftAntiForgettingError(f"{name} {field} mismatch")
    return supplied


def _path(raw: object, *, relative_to: Path, name: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise P13StateShiftAntiForgettingError(f"{name} path is missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = relative_to.parent / path
    path = path.resolve()
    if not path.is_file():
        raise P13StateShiftAntiForgettingError(f"{name} is not a file")
    return path


def _logical_digest(conn: sqlite3.Connection) -> str:
    return _digest("\n".join(conn.iterdump()))


def _authority_closed(payload: Mapping, name: str) -> None:
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") not in {
                None, "not_attempted"} or
            payload.get("memory_docs_submitted") not in {None, False}):
        raise P13StateShiftAntiForgettingError(
            f"{name} crossed an authority boundary")


def _write_report(path: Path, payload: dict) -> dict:
    identity_digest = _digest(payload)
    payload["receipt_id"] = (
        "p13_state_shift_" + payload["gate"].replace("-", "_") + "_" +
        identity_digest.split(":", 1)[1][:24])
    payload["report_digest"] = _digest(payload)
    if path.exists():
        raise P13StateShiftAntiForgettingError(
            f"immutable anti-forgetting output exists: {path}")
    with path.open("x") as stream:
        stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _typed_expansion(payload: Mapping) -> tuple[
        MechanismKnowledge, MechanismKnowledge, SupportEnvelope,
        SupportEnvelope, StateShiftSupportExpansionReceipt]:
    try:
        parent = MechanismKnowledge.from_dict(payload.get("parent_knowledge"))
        child = MechanismKnowledge.from_dict(payload.get("child_knowledge"))
        parent_envelope = SupportEnvelope.from_dict(
            payload.get("parent_support_envelope"))
        child_envelope = SupportEnvelope.from_dict(
            payload.get("child_support_envelope"))
        receipt = StateShiftSupportExpansionReceipt.from_dict(
            payload.get("support_expansion_receipt"))
    except (KeyError, TypeError, ValueError) as exc:
        raise P13StateShiftAntiForgettingError(
            f"support expansion typed payload is invalid: {exc}") from exc
    if (receipt.parent_knowledge_digest != parent.content_digest or
            receipt.child_knowledge_digest != child.content_digest or
            receipt.parent_support_envelope_digest !=
            parent_envelope.envelope_digest or
            receipt.child_support_envelope_digest !=
            child_envelope.envelope_digest):
        raise P13StateShiftAntiForgettingError(
            "support expansion receipt does not bind its typed objects")
    return parent, child, parent_envelope, child_envelope, receipt


def _preserved_claim(parent: MechanismKnowledge,
                     child: MechanismKnowledge) -> dict:
    fields = (
        "knowledge_id", "mechanism_family", "compatibility_profile",
        "antecedent", "intervention", "mediated_effects", "expected_outcome",
        "negative_applicability", "preserved_obligations",
        "known_failure_modes", "causal_path_ids", "evidence_level",
    )
    unchanged = all(
        stable_dumps(getattr(parent, name)) == stable_dumps(getattr(child, name))
        for name in fields)
    parent_positive = {stable_dumps(item) for item in parent.positive_applicability}
    child_positive = {stable_dumps(item) for item in child.positive_applicability}
    lineages_preserved = set(parent.support_lineages) <= set(child.support_lineages)
    if (not unchanged or not parent_positive <= child_positive or
            not lineages_preserved or child.version != parent.version + 1 or
            child.status != "shadow"):
        raise P13StateShiftAntiForgettingError(
            "child Knowledge changes semantics outside support expansion")
    return {
        "causal_semantics_unchanged": unchanged,
        "parent_positive_applicability_preserved": True,
        "parent_support_lineages_preserved": lineages_preserved,
        "version_increment_delta": f"{parent.object_id}->{child.object_id}",
    }


def _support_preserved(parent: SupportEnvelope,
                       child: SupportEnvelope) -> dict:
    if (parent.source_transition_ids != child.source_transition_ids or
            not set(parent.evidence_refs) <= set(child.evidence_refs)):
        raise P13StateShiftAntiForgettingError(
            "child support envelope drops parent evidence")
    checks = {}
    for name, raw in parent.dimensions.items():
        child_raw = child.dimensions.get(name)
        if not isinstance(raw, Mapping) or not isinstance(child_raw, Mapping):
            raise P13StateShiftAntiForgettingError(
                "support envelope dimension is malformed")
        before = {stable_dumps(item) for item in raw.get("values", ())}
        after = {stable_dumps(item) for item in child_raw.get("values", ())}
        checks[name] = before <= after
    if not checks or not all(checks.values()):
        raise P13StateShiftAntiForgettingError(
            "child support envelope drops parent support")
    return checks


def _transition_evidence(conn: sqlite3.Connection, transition_ids: Sequence[str],
                         campaign_id: str) -> tuple[list[dict], set[str]]:
    evidence = []
    lineages = set()
    for transition_id in sorted(transition_ids):
        try:
            require_verified_transition(conn, transition_id)
        except ValueError as exc:
            raise P13StateShiftAntiForgettingError(str(exc)) from exc
        row = conn.execute(
            """SELECT t.action_domain, t.action_json, t.outcome,
                      t.created_regressions_json, t.verifier_json,
                      s.lineage_id
                 FROM tehm_transitions AS t
                 JOIN tehm_states AS s ON s.state_id=t.source_state_id
                WHERE t.transition_id=?""", (transition_id,)).fetchone()
        membership = conn.execute(
            """SELECT split, learner_eligible FROM tehm_dataset_membership
                WHERE campaign_id=? AND transition_id=?""",
            (campaign_id, transition_id)).fetchone()
        if row is None or membership is None:
            raise P13StateShiftAntiForgettingError(
                "parent transition or training membership is missing")
        action = json.loads(row["action_json"])
        regressions = json.loads(row["created_regressions_json"] or "[]")
        verifier = json.loads(row["verifier_json"])
        scoped = verifier.get("scoped_execution")
        pair = scoped.get("pair_receipt") if isinstance(scoped, Mapping) else None
        if (row["action_domain"] != "flow.CONFIG_DELTA" or
                action.get("transformation_family") != "DENSITY_RELIEF" or
                row["outcome"] != "PASS" or regressions or
                tuple(membership) != ("training", 1) or
                verifier.get("verdict") != "PASS" or
                verifier.get("oracle_complete") is not True or
                not isinstance(pair, Mapping) or
                pair.get("controlled_measurement_valid") is not True or
                pair.get("before", {}).get("verdict") != "FAIL" or
                pair.get("after", {}).get("verdict") != "PASS"):
            raise P13StateShiftAntiForgettingError(
                "parent transition no longer replays as a safe controlled repair")
        lineage = row["lineage_id"]
        if type(lineage) is not str or not lineage:
            raise P13StateShiftAntiForgettingError(
                "parent transition lineage is missing")
        lineages.add(lineage)
        evidence.append({
            "transition_id": transition_id,
            "lineage_id": lineage,
            "pair_receipt_digest": pair.get("receipt_digest"),
            "before_execution_digest": pair["before"].get("execution_digest"),
            "after_execution_digest": pair["after"].get("execution_digest"),
            "verdict": "PASS",
        })
    if len(lineages) < 2:
        raise P13StateShiftAntiForgettingError(
            "parent replay requires two distinct lineages")
    return evidence, lineages


def _heldout_audit(registration: Mapping, registration_path: Path, *,
                   campaign_id: str, child: MechanismKnowledge,
                   child_envelope: SupportEnvelope, resolved_state: Mapping,
                   forbidden_lineages: set[str]) -> dict:
    _authority_closed(registration, "held-out registration")
    if (registration.get("version") != HELDOUT_VERSION or
            registration.get("campaign_id") != campaign_id or
            registration.get("dataset_split") != "heldout" or
            registration.get("learner_eligible") is not False or
            registration.get("execution_attempted") is not False or
            registration.get("expected_decision") != "NO_SKILL" or
            registration.get("expected_reason") != "STATE_SHIFT"):
        raise P13StateShiftAntiForgettingError(
            "held-out registration does not close the evaluation firewall")
    lineage = registration.get("lineage_id")
    if type(lineage) is not str or not lineage or lineage in forbidden_lineages:
        raise P13StateShiftAntiForgettingError(
            "held-out lineage is missing or overlaps training")
    raw_inputs = registration.get("source_inputs")
    if (not isinstance(raw_inputs, Sequence) or
            isinstance(raw_inputs, (str, bytes)) or not raw_inputs):
        raise P13StateShiftAntiForgettingError(
            "held-out registration requires source inputs")
    source_inputs = []
    for raw in raw_inputs:
        if not isinstance(raw, Mapping):
            raise P13StateShiftAntiForgettingError(
                "held-out source input is malformed")
        path = _path(raw.get("path"), relative_to=registration_path,
                     name="held-out source input")
        actual = _sha256(path)
        supplied = str(raw.get("sha256") or "")
        if supplied.removeprefix("sha256:") != actual.removeprefix("sha256:"):
            raise P13StateShiftAntiForgettingError(
                "held-out source input digest mismatch")
        source_inputs.append({"path": str(path), "sha256": actual})
    context = registration.get("current_context")
    if not isinstance(context, Mapping):
        raise P13StateShiftAntiForgettingError(
            "held-out current context is missing")
    input_digests = {item["sha256"].removeprefix("sha256:")
                     for item in source_inputs}
    structural = context.get("structural_signature")
    structural_digests = structural.get("rtl_sha256") if isinstance(
        structural, Mapping) else None
    if (not isinstance(structural_digests, Sequence) or
            isinstance(structural_digests, (str, bytes)) or
            not set(structural_digests) <= input_digests):
        raise P13StateShiftAntiForgettingError(
            "held-out structural context is not bound to source inputs")
    shift = evaluate_state_shift(
        context, resolved_state, child, child_envelope,
        evidence_refs=tuple(item["sha256"] for item in source_inputs))
    passed = bool(
        shift.transferable is False and shift.reason == "STATE_SHIFT" and
        "structural_shift" in shift.shifted_dimensions)
    if not passed:
        raise P13StateShiftAntiForgettingError(
            "held-out source was incorrectly admitted by the expanded envelope")
    return {
        "registration": str(registration_path),
        "registration_sha256": _sha256(registration_path),
        "case_id": registration.get("case_id"),
        "lineage_id": lineage,
        "source_inputs": source_inputs,
        "current_context_digest": shift.current_context_digest,
        "state_shift": shift.to_dict(),
        "routing_decision": "NO_SKILL",
        "no_skill_reason": "STATE_SHIFT",
        "memory_action_executed": False,
        "repair_success_claimed": False,
        "passed": passed,
    }


def build_p13_state_shift_anti_forgetting_evidence(
        support_expansion_report: Path | str,
        heldout_registration: Path | str, *, output_dir: Path | str) -> dict:
    expansion_path = Path(support_expansion_report).expanduser().resolve()
    heldout_path = Path(heldout_registration).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise P13StateShiftAntiForgettingError(
            f"immutable anti-forgetting directory exists: {output}")
    expansion = _load(expansion_path, "support expansion report")
    expansion_digest = _content_digest(
        expansion, "report_digest", "support expansion report")
    _authority_closed(expansion, "support expansion report")
    if (expansion.get("support_expansion_derived") is not True or
            expansion.get("target_replay", {}).get("passed") is not True or
            expansion.get("shadow_update_attempted") is not False):
        raise P13StateShiftAntiForgettingError(
            "support expansion is not at the anti-forgetting boundary")
    parent, child, parent_envelope, child_envelope, receipt = (
        _typed_expansion(expansion))
    campaign_id = receipt.campaign_id
    claim_checks = _preserved_claim(parent, child)
    support_checks = _support_preserved(parent_envelope, child_envelope)

    source = expansion.get("source_database")
    if not isinstance(source, Mapping):
        raise P13StateShiftAntiForgettingError(
            "support expansion source database is missing")
    source_path = _path(source.get("path"), relative_to=expansion_path,
                        name="source database")
    physical_before = _sha256(source_path)
    if physical_before != source.get("sha256"):
        raise P13StateShiftAntiForgettingError(
            "support expansion source database file changed")
    conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    staging = None
    replay_conn = None
    try:
        logical_before = _logical_digest(conn)
        if logical_before != source.get("logical_digest"):
            raise P13StateShiftAntiForgettingError(
                "support expansion source database semantics changed")
        source_snapshot_ref = expansion.get("source_snapshot_report")
        if not isinstance(source_snapshot_ref, Mapping):
            raise P13StateShiftAntiForgettingError(
                "support expansion source snapshot binding is missing")
        snapshot_path = _path(
            source_snapshot_ref.get("path"), relative_to=expansion_path,
            name="source snapshot report")
        if _sha256(snapshot_path) != source_snapshot_ref.get("sha256"):
            raise P13StateShiftAntiForgettingError(
                "source snapshot file binding mismatch")
        snapshot = _load(snapshot_path, "source snapshot report")
        snapshot_digest = _content_digest(
            snapshot, "report_digest", "source snapshot report")
        if snapshot_digest != source_snapshot_ref.get("report_digest"):
            raise P13StateShiftAntiForgettingError(
                "source snapshot content binding mismatch")
        replay = snapshot.get("replay")
        resolved_state = replay.get("resolved_source_state") if isinstance(
            replay, Mapping) else None
        training_campaign = replay.get("campaign_id") if isinstance(
            replay, Mapping) else None
        if not isinstance(resolved_state, Mapping) or type(
                training_campaign) is not str:
            raise P13StateShiftAntiForgettingError(
                "source snapshot replay state is missing")
        acquisition_ref = snapshot.get("parent_acquisitions")
        if not isinstance(acquisition_ref, Mapping):
            raise P13StateShiftAntiForgettingError(
                "source snapshot acquisition binding is missing")
        acquisition_path = _path(
            acquisition_ref.get("path"), relative_to=snapshot_path,
            name="parent acquisitions")
        if _sha256(acquisition_path) != acquisition_ref.get("sha256"):
            raise P13StateShiftAntiForgettingError(
                "parent acquisition file binding mismatch")
        acquisition = _load(acquisition_path, "parent acquisitions")
        acquisitions = acquisition.get("acquisitions")
        acquisition_digest = acquisition.get("digest")
        if (not isinstance(acquisitions, Mapping) or
                acquisition_digest != _digest(acquisitions) or
                acquisition_digest != acquisition_ref.get(
                    "acquisition_digest")):
            raise P13StateShiftAntiForgettingError(
                "parent acquisition content binding mismatch")
        replay_conn = sqlite3.connect(":memory:")
        replay_conn.row_factory = sqlite3.Row
        replay_conn.execute("PRAGMA foreign_keys=ON")
        conn.backup(replay_conn)
        with scoped_learning_replay(
                replay_conn, campaign_id=training_campaign,
                acquisitions=acquisitions,
                expected_digest=acquisition_digest):
            transition_evidence, parent_lineages = _transition_evidence(
                replay_conn, parent_envelope.source_transition_ids,
                training_campaign)

        staging = sqlite3.connect(":memory:")
        staging.row_factory = sqlite3.Row
        conn.backup(staging)
        staging_before = _logical_digest(staging)
        staging.execute("SAVEPOINT p13_rollback_preflight")
        staging.execute(
            "CREATE TABLE tehm_p13_rollback_sentinel(value TEXT NOT NULL)")
        staging.execute(
            "INSERT INTO tehm_p13_rollback_sentinel VALUES ('discard-me')")
        staging.execute("ROLLBACK TO SAVEPOINT p13_rollback_preflight")
        staging.execute("RELEASE SAVEPOINT p13_rollback_preflight")
        staging_after = _logical_digest(staging)
    finally:
        if staging is not None:
            staging.close()
        if replay_conn is not None:
            replay_conn.close()
        conn.close()
    physical_after = _sha256(source_path)
    readback = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        logical_after = _logical_digest(readback)
    finally:
        readback.close()
    rollback = build_isolated_rollback_receipt(
        source_digest_before=logical_before,
        source_digest_after=logical_after,
        staging_digest_before=staging_before,
        staging_digest_after=staging_after,
        staging_discarded=True)
    if (physical_after != physical_before or rollback.verified is not True or
            staging_after != staging_before):
        raise P13StateShiftAntiForgettingError(
            "isolated rollback preflight failed")

    heldout = _load(heldout_path, "held-out registration")
    heldout_details = _heldout_audit(
        heldout, heldout_path, campaign_id=campaign_id, child=child,
        child_envelope=child_envelope, resolved_state=resolved_state,
        forbidden_lineages={*parent_lineages, *receipt.case_lineages.values()})

    output.mkdir(parents=True)
    common = {
        "version": REPORT_VERSION,
        "campaign_id": campaign_id,
        "support_expansion_report": {
            "path": str(expansion_path), "sha256": _sha256(expansion_path),
            "report_digest": expansion_digest,
            "receipt_digest": receipt.receipt_digest,
        },
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    non_target = _write_report(output / "non-target-regression.json", {
        **common,
        "gate": "non-target-regression",
        "parent_knowledge_object_id": parent.object_id,
        "child_knowledge_object_id": child.object_id,
        "claim_checks": claim_checks,
        "support_dimension_preservation": support_checks,
        "source_training_campaign": training_campaign,
        "transition_evidence": transition_evidence,
        "distinct_lineage_count": len(parent_lineages),
        "regression_free": True,
    })
    heldout_report = _write_report(output / "heldout-audit.json", {
        **common,
        "gate": "heldout-audit",
        "audit_scope": "memory_applicability_firewall",
        "heldout": heldout_details,
        "passed": True,
    })
    rollback_report = _write_report(output / "rollback.json", {
        **common,
        "gate": "rollback",
        "source_database": {
            "path": str(source_path),
            "sha256_before": physical_before,
            "sha256_after": physical_after,
            "logical_digest_before": logical_before,
            "logical_digest_after": logical_after,
            "opened_read_only": True,
        },
        "rollback": rollback.to_dict(),
        "verified": True,
    })
    target_id = receipt.receipt_id
    manifest = {
        "version": MANIFEST_VERSION,
        "campaign_id": campaign_id,
        "case_id": "state-shift-support-expansion:u50",
        "target_replay": {
            "receipt_id": target_id,
            "path": str(expansion_path),
            "sha256": _sha256(expansion_path),
            "passed": True,
        },
        "non_target_regression": {
            "receipt_id": non_target["receipt_id"],
            "path": str(output / "non-target-regression.json"),
            "sha256": _sha256(output / "non-target-regression.json"),
            "regression_free": True,
        },
        "heldout_audit": {
            "receipt_id": heldout_report["receipt_id"],
            "path": str(output / "heldout-audit.json"),
            "sha256": _sha256(output / "heldout-audit.json"),
            "passed": True,
        },
        "rollback": {
            "receipt_id": rollback_report["receipt_id"],
            "pointer": f"isolated-source:{source_path}",
            "path": str(output / "rollback.json"),
            "sha256": _sha256(output / "rollback.json"),
            "verified": True,
        },
    }
    manifest_path = output / "anti-forgetting-manifest.json"
    with manifest_path.open("x") as stream:
        stream.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {
        "campaign_id": campaign_id,
        "target_replay_passed": True,
        "non_target_regression_free": True,
        "heldout_audit_passed": True,
        "rollback_verified": True,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--support-expansion-report", type=Path, required=True)
    parser.add_argument("--heldout-registration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_p13_state_shift_anti_forgetting_evidence(
            args.support_expansion_report, args.heldout_registration,
            output_dir=args.output_dir)
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
