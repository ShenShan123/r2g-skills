#!/usr/bin/env python3
"""Read-only audit of bounded ORFS support cohorts.

The auditor consumes campaign-local staging stores and never imports or mutates
canonical memory.  It independently rechecks the persisted full-oracle receipt
on each captured transition, separates harmful observations from the selected
support cohort, and reports the six rule-promotion gates with explicit
``NOT_ESTABLISHED`` status where no independent authority evidence exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MEMORY_ROOT))

from tehm.batch_lane import canonical_snapshots  # noqa: E402
from tehm.causal.transfer import full_oracle_complete  # noqa: E402
from tehm.causal.transfer_ledger import (  # noqa: E402
    load_causal_transfer_receipt, verify_causal_transfer)
from tehm.dataset import validate_membership_row  # noqa: E402

GATES = (
    "rollback_verified", "registry_verified", "obligation_coverage",
    "cross_lineage_te", "harmful_rate", "conformal_coverage",
)


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _json(raw, fallback=None):
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {} if fallback is None else fallback
    return value


def _strict_string_vector(value, *, label: str, allow_empty: bool = False) -> list[str]:
    """Require a canonical JSON vector of non-empty strings.

    Transfer receipts are content-addressed, but their lineage projections are
    still an input boundary for this audit.  Do not let ``str(...)`` turn a
    malformed value into a lineage that happens to match the support cohort.
    """
    if not isinstance(value, list):
        raise ValueError(f"{label}_malformed")
    if not allow_empty and not value:
        raise ValueError(f"{label}_empty")
    if any(type(item) is not str or not item.strip() for item in value):
        raise ValueError(f"{label}_malformed")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label}_duplicate")
    if normalized != sorted(normalized):
        raise ValueError(f"{label}_not_canonical")
    return normalized


def _audit_transfer_witness(
        ledger_db: Path | None, receipt_ids, *, selected_lineages: set[str],
        selected_families: set[str]) -> dict:
    """Replay optional L4 transfer receipts against this support cohort.

    The support auditor is read-only.  A transfer receipt can establish
    ``cross_lineage_te`` here only when the ledger replay is verified, the
    receipt is an eligible L4 result, its training lineages are a subset of
    the selected support cohort, its held-out lineages are disjoint, and the
    path mechanism family agrees with the cohort.  Missing optional input is
    ``NOT_ESTABLISHED``; supplied but malformed/mismatched evidence is
    ``FAIL``.  No summary boolean is accepted as authority.
    """
    if ledger_db is None and receipt_ids in (None, (), []):
        return {
            "gate_status": "NOT_ESTABLISHED", "receipt_ids": [],
            "receipts": [], "errors": [], "training_lineages": [],
            "transfer_lineages": [], "mechanism_families": [],
        }
    if ledger_db is None or not isinstance(receipt_ids, (list, tuple)):
        return {
            "gate_status": "FAIL", "receipt_ids": list(receipt_ids or []),
            "receipts": [], "errors": ["transfer_input_incomplete"],
            "training_lineages": [], "transfer_lineages": [],
            "mechanism_families": [],
        }
    ids = list(receipt_ids)
    if (not ids or any(type(value) is not str or not value.strip()
                       for value in ids) or len(set(ids)) != len(ids)):
        return {
            "gate_status": "FAIL", "receipt_ids": ids, "receipts": [],
            "errors": ["transfer_receipt_ids_malformed"],
            "training_lineages": [], "transfer_lineages": [],
            "mechanism_families": [],
        }
    db_path = Path(ledger_db).resolve()
    if not db_path.is_file():
        return {
            "gate_status": "FAIL", "ledger_db": str(db_path),
            "receipt_ids": ids, "receipts": [],
            "errors": ["transfer_ledger_missing"],
            "training_lineages": [], "transfer_lineages": [],
            "mechanism_families": [],
        }
    try:
        # ``immutable=1`` prevents SQLite from creating WAL/SHM sidecars while
        # auditing a supposedly immutable campaign snapshot.  A read-only
        # authority audit must not mutate the evidence bundle merely by
        # opening it.
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return {
            "gate_status": "FAIL", "ledger_db": str(db_path),
            "receipt_ids": ids, "receipts": [],
            "errors": [f"transfer_ledger_open_failed:{exc}"],
            "training_lineages": [], "transfer_lineages": [],
            "mechanism_families": [],
        }
    rows = []
    errors = []
    training_lineages: set[str] = set()
    transfer_lineages: set[str] = set()
    mechanism_families: set[str] = set()
    try:
        for receipt_id in ids:
            detail = {"receipt_id": receipt_id, "verified": False,
                      "eligible": False, "reasons": []}
            try:
                ledger = load_causal_transfer_receipt(conn, receipt_id)
                if ledger is None:
                    raise ValueError("transfer_receipt_missing")
                checked = verify_causal_transfer(conn, ledger.to_dict())
                detail.update({
                    "verified": checked.get("verified") is True,
                    "eligible": checked.get("eligible") is True,
                    "evidence_level": checked.get("evidence_level"),
                    "path_id": checked.get("path_id"),
                    "reasons": list(checked.get("reasons") or []),
                })
                if (checked.get("verified") is not True or
                        checked.get("eligible") is not True or
                        checked.get("evidence_level") !=
                        "L4_TRANSFER_SUPPORTED_MECHANISM"):
                    raise ValueError("transfer_receipt_not_verified_l4")
                transfer = ledger.transfer_receipt
                train = _strict_string_vector(
                    transfer.get("training_lineages"),
                    label="training_lineages")
                heldout = _strict_string_vector(
                    transfer.get("transfer_lineages"),
                    label="transfer_lineages")
                path_id = ledger.path_id
                path = conn.execute(
                    "SELECT mechanism_family FROM tehm_causal_paths "
                    "WHERE path_id=?", (path_id,)).fetchone()
                if path is None or type(path["mechanism_family"]) is not str:
                    raise ValueError("transfer_path_mechanism_missing")
                family = path["mechanism_family"].strip()
                if not family:
                    raise ValueError("transfer_path_mechanism_missing")
                if not selected_families or family not in selected_families:
                    raise ValueError("transfer_mechanism_cohort_mismatch")
                if len(set(train)) < 2:
                    raise ValueError("transfer_training_lineages_insufficient")
                if not set(train).issubset(selected_lineages):
                    raise ValueError("transfer_training_lineage_cohort_mismatch")
                if set(heldout) & selected_lineages:
                    raise ValueError("transfer_heldout_lineage_not_disjoint")
                training_lineages.update(train)
                transfer_lineages.update(heldout)
                mechanism_families.add(family)
                detail.update({"training_lineages": train,
                               "transfer_lineages": heldout,
                               "mechanism_family": family})
            except (TypeError, ValueError, KeyError, sqlite3.Error) as exc:
                reason = str(exc)
                detail.setdefault("reasons", []).append(reason)
                errors.append(f"{receipt_id}:{reason}")
            rows.append(detail)
    finally:
        conn.close()
    status = ("PASS" if rows and not errors and len(training_lineages) >= 2
              and transfer_lineages else "FAIL")
    return {
        "ledger_db": str(db_path),
        "receipt_ids": ids,
        "receipts": rows,
        "errors": sorted(set(errors)),
        "training_lineages": sorted(training_lineages),
        "transfer_lineages": sorted(transfer_lineages),
        "mechanism_families": sorted(mechanism_families),
        "gate_status": status,
    }


def _staging_db(root: Path) -> Path:
    candidates = (
        root / "staging" / "complete" / "tehm.sqlite",
        root / "staging" / "full" / "tehm.sqlite",
        root / "staging" / "tehm.sqlite",
    )
    ranked = []
    for order, path in enumerate(candidates):
        if not path.is_file():
            continue
        # A campaign may have a legacy partial capture beside a later
        # full-oracle recapture.  Prefer the DB with the most persisted
        # expanded receipts, then use the stable candidate order.
        try:
            conn = sqlite3.connect(
                f"file:{path}?mode=ro&immutable=1", uri=True)
            full = conn.execute(
                "SELECT COUNT(*) FROM tehm_transitions "
                "WHERE verifier_json LIKE '%full_oracle%'").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]
            conn.close()
        except sqlite3.Error:
            continue
        ranked.append((int(full), int(total), -order, path))
    if ranked:
        return max(ranked)[-1]
    raise FileNotFoundError(f"no campaign staging DB below {root / 'staging'}")


def _campaign(root: Path, *, selected: bool) -> dict:
    root = root.resolve()
    manifest_path = root / "campaign_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    item_by_transition = {
        row.get("transition_id"): row
        for row in manifest.get("captured", [])
        if row.get("transition_id")
    }
    db = _staging_db(root)
    # Keep the support audit side-effect free even when the input DB is a WAL
    # snapshot.  The authority decision must never depend on SQLite creating
    # a new ``-wal``/``-shm`` file during a read.
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT t.transition_id, t.observation_delta_json, t.verifier_json, "
            "t.provenance_json, t.action_json, p.deltas_json "
            "FROM tehm_transitions t LEFT JOIN tehm_physical_effects p "
            "ON p.transition_id=t.transition_id ORDER BY t.transition_id"
        ).fetchall()
        membership_table_present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='tehm_dataset_membership'"
        ).fetchone() is not None
        observations = []
        for row in rows:
            transition_id = row["transition_id"]
            captured = item_by_transition.get(transition_id, {})
            delta = _json(row["observation_delta_json"])
            verifier = _json(row["verifier_json"])
            full_complete = full_oracle_complete(verifier)
            utility = (delta.get("utility_verdict")
                       if type(delta.get("utility_verdict")) is str
                       else "UNKNOWN")
            action = _json(row["action_json"], fallback={})
            manifest_family = captured.get("family")
            action_family = action.get("transformation_family")
            mechanism_family = (manifest_family if type(manifest_family) is str
                                and manifest_family.strip() else action_family)

            # The manifest is only a proposal/index.  The staging DB's
            # membership row is the evidence-firewall authority.  A support
            # audit must be able to replay exactly one membership and it must
            # be training + learner-eligible; otherwise a complete
            # calibration/held-out row could be misused as learner support.
            membership = []
            if membership_table_present:
                membership = conn.execute(
                    "SELECT campaign_id, split, learner_eligible "
                    "FROM tehm_dataset_membership WHERE transition_id=? "
                    "ORDER BY campaign_id",
                    (transition_id,),
                ).fetchall()
            membership_campaign_id = None
            membership_split = None
            membership_eligible = None
            firewall_reasons = []
            if selected and not captured:
                firewall_reasons.append("manifest_capture_missing")
            if not membership_table_present:
                firewall_reasons.append("membership_table_missing")
            elif len(membership) != 1:
                firewall_reasons.append(
                    "membership_missing" if not membership
                    else "membership_ambiguous")
            else:
                membership_row = membership[0]
                membership_campaign_id = membership_row["campaign_id"]
                if (type(membership_campaign_id) is not str or
                        not membership_campaign_id.strip()):
                    firewall_reasons.append("membership_campaign_id_malformed")
                try:
                    membership_eligible, membership_split = \
                        validate_membership_row(membership_row)
                except ValueError as exc:
                    firewall_reasons.append(f"membership_invalid:{exc}")
                else:
                    if (membership_split != "training" or
                            membership_eligible is not True):
                        firewall_reasons.append(
                            "membership_not_training_learner")

            if selected:
                manifest_split = captured.get("dataset_split")
                manifest_eligible = captured.get("learner_eligible")
                if type(manifest_split) is not str:
                    firewall_reasons.append("manifest_split_missing_or_malformed")
                elif membership_split is not None and manifest_split != membership_split:
                    firewall_reasons.append("manifest_membership_split_mismatch")
                if type(manifest_eligible) is not bool:
                    firewall_reasons.append(
                        "manifest_learner_eligible_missing_or_malformed")
                elif membership_eligible is not None and manifest_eligible != membership_eligible:
                    firewall_reasons.append(
                        "manifest_membership_learner_flag_mismatch")

            observations.append({
                "case_id": captured.get("case_id"),
                "transition_id": transition_id,
                "lineage_id": captured.get("lineage_id"),
                "dataset_split": captured.get("dataset_split"),
                "learner_eligible": captured.get("learner_eligible"),
                "membership_campaign_id": membership_campaign_id,
                "membership_split": membership_split,
                "membership_learner_eligible": membership_eligible,
                "support_eligible": selected and not firewall_reasons,
                "support_firewall_reasons": sorted(set(firewall_reasons)),
                "oracle_complete": verifier.get("oracle_complete") is True,
                "full_oracle_complete": full_complete,
                "utility_verdict": utility,
                "run_id": (_json(row["provenance_json"]).get("run_id")
                           or _json(row["provenance_json"]).get("record_id")),
                "mechanism_family": mechanism_family,
                "deltas": _json(row["deltas_json"], fallback={}),
            })
    finally:
        conn.close()
    complete = [row for row in observations
                if row["oracle_complete"] and row["full_oracle_complete"]]
    selected_rows = [row for row in complete
                     if row["support_eligible"] and
                     row["utility_verdict"] != "HARMFUL"] if selected else []
    harmful = [row for row in complete if row["utility_verdict"] == "HARMFUL"]
    firewall_errors = [
        {
            "transition_id": row["transition_id"],
            "case_id": row.get("case_id"),
            "reasons": row["support_firewall_reasons"],
        }
        for row in observations
        if selected and row["support_firewall_reasons"]
    ]
    return {
        "campaign_root": str(root),
        "campaign_manifest_sha256": _sha(manifest_path),
        "source_freeze_digest": manifest.get("source_freeze_digest"),
        "staging_db": str(db),
        "staging_db_sha256": _sha(db),
        "observations": observations,
        "complete_count": len(complete),
        "incomplete_count": len(observations) - len(complete),
        "selected_support_count": len(selected_rows),
        "harmful_complete_count": len(harmful),
        "selected": bool(selected),
        "support_firewall_status": "FAIL" if firewall_errors else "PASS",
        "support_firewall_errors": firewall_errors,
    }


def audit(roots: list[Path], *, negative_roots: list[Path],
          transfer_ledger_db: Path | None = None,
          transfer_receipt_ids: list[str] | tuple[str, ...] = ()) -> dict:
    selected_campaigns = [_campaign(path, selected=True) for path in roots]
    negative_campaigns = [_campaign(path, selected=False) for path in negative_roots]
    selected = [row for campaign in selected_campaigns
                for row in campaign["observations"]
                if row["oracle_complete"] and row["full_oracle_complete"]
                and row["support_eligible"]
                and row["utility_verdict"] != "HARMFUL"]
    lineages = sorted({row["lineage_id"] for row in selected if row.get("lineage_id")})
    harmful = [row for campaign in selected_campaigns
               for row in campaign["observations"]
               if row["oracle_complete"] and row["full_oracle_complete"]
               and row["support_eligible"]
               and row["utility_verdict"] == "HARMFUL"]
    support_firewall_errors = [
        {
            "campaign_root": campaign["campaign_root"],
            "errors": campaign["support_firewall_errors"],
        }
        for campaign in selected_campaigns
        if campaign["support_firewall_errors"]
    ]
    selected_families = {
        row["mechanism_family"] for row in selected
        if type(row.get("mechanism_family")) is str
        and row["mechanism_family"].strip()
    }
    transfer_evidence = _audit_transfer_witness(
        transfer_ledger_db, transfer_receipt_ids,
        selected_lineages=set(lineages), selected_families=selected_families)
    negative_harmful = [row for campaign in negative_campaigns
                        for row in campaign["observations"]
                        if row["oracle_complete"] and row["full_oracle_complete"]
                        and row["utility_verdict"] == "HARMFUL"]
    complete_count = sum(campaign["complete_count"]
                         for campaign in selected_campaigns)
    incomplete_count = sum(campaign["incomplete_count"]
                           for campaign in selected_campaigns)
    # These are deliberately independent of the caller's desired decision.
    # Training-only observations cannot establish held-out transfer, rollback,
    # registry, or conformal evidence.
    gate_status = {
        "rollback_verified": "NOT_ESTABLISHED",
        "registry_verified": "NOT_ESTABLISHED",
        "obligation_coverage": "PASS" if selected and incomplete_count == 0 and all(
            row["full_oracle_complete"] for row in selected) else "NOT_ESTABLISHED",
        "cross_lineage_te": transfer_evidence["gate_status"],
        "harmful_rate": "PASS" if selected and not harmful else (
            "FAIL" if harmful else "NOT_ESTABLISHED"),
        "conformal_coverage": "NOT_ESTABLISHED",
    }
    report = {
        "version": "tehm-orfs-support-cohort-audit-v1",
        "campaigns": selected_campaigns,
        "negative_control_campaigns": negative_campaigns,
        "support_observation_count": len(selected),
        "complete_observation_count": complete_count,
        "incomplete_observation_count": incomplete_count,
        "unique_lineages": lineages,
        "unique_lineage_count": len(lineages),
        "harmful_complete_count": len(harmful),
        "negative_control_harmful_count": len(negative_harmful),
        "harmful_rate": (len(harmful) / len(selected) if selected else None),
        "gate_status": gate_status,
        "gates": {name: status == "PASS" for name, status in gate_status.items()},
        "support_firewall_status": "FAIL" if support_firewall_errors else "PASS",
        "support_firewall_errors": support_firewall_errors,
        "all_gates_established": (not support_firewall_errors and
                                   all(status == "PASS"
                                       for status in gate_status.values())),
        "decision": "ALLOW_AUTHORITY_REVIEW" if (
            not support_firewall_errors and
            all(status == "PASS" for status in gate_status.values()))
        else "DENY_CANONICAL_IMPORT",
        "promotion_attempted": False,
        "canonical_memory_mutation": "none",
        "canonical_snapshots": canonical_snapshots(),
        "cross_lineage_evidence": transfer_evidence,
    }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path,
                        help="campaign roots supplying candidate support observations")
    parser.add_argument("--negative-root", action="append", type=Path, default=[],
                        help="complete campaign root retained as an excluded negative control")
    parser.add_argument("--transfer-ledger-db", type=Path, default=None,
                        help="read-only shadow DB containing replayable L4 transfer receipts")
    parser.add_argument("--transfer-receipt-id", dest="transfer_receipt_ids",
                        action="append", default=[],
                        help="replay-verified L4 receipt ID (repeatable)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit(
        args.roots, negative_roots=args.negative_root,
        transfer_ledger_db=args.transfer_ledger_db,
        transfer_receipt_ids=args.transfer_receipt_ids)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "support_observation_count": report["support_observation_count"],
        "unique_lineage_count": report["unique_lineage_count"],
        "gate_status": report["gate_status"],
        "decision": report["decision"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
