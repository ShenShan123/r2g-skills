"""Replayable paired physical-harm attribution for ORFS P12 receipts.

An ORFS arm can report PPA, but it cannot decide whether memory helped or
hurt without seeing the no-memory counterfactual.  This module performs that
comparison after all four P12 arms exist.  The utility contract must have been
bound into the frozen campaign manifest; this code never selects a contract
from the observed result and never grants lifecycle or production authority.
"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import replace

from tehm.ids import stable_dumps
from tehm.physical.effects import extract_deltas
from tehm.physical.utility_contracts import (
    action_contract_binding_reason,
    evaluate_observed_contract,
    known_utility_contracts,
    utility_contract_digest,
    validate_utility_contract,
)
from tehm.retrieval.structured_candidate import StructuredRepairCandidate

from .candidate_executor import (
    P12_ARMS,
    CandidateExecutionReceipt,
    PairedCandidateExecutionReceipt,
)
from .counterfactual_oracle import replay_counterfactual_oracle_receipt
from .orfs_candidate_oracle import ORFS_PHYSICAL_OBSERVATION_VERSION


ORFS_PAIRED_UTILITY_VERSION = "orfs-p12-paired-utility-v1"
_MEMORY_ARMS = P12_ARMS[1:]
_POSITIVE_OUTCOMES = frozenset({"PASS", "PARTIAL"})


class OrfsPairedUtilityError(ValueError):
    """A paired physical observation or contract binding is malformed."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _physical(receipt: CandidateExecutionReceipt) -> dict:
    raw = (receipt.metadata.get("oracle_metadata") or {}).get(
        "physical_observation")
    if not isinstance(raw, Mapping):
        raise OrfsPairedUtilityError(
            f"{receipt.case_id}/{receipt.metadata.get('arm')} lacks physical observation")
    if raw.get("version") != ORFS_PHYSICAL_OBSERVATION_VERSION:
        raise OrfsPairedUtilityError("ORFS physical observation version mismatch")
    if (raw.get("evaluation_only") is not True or
            raw.get("canonical_memory_mutation") != "none" or
            raw.get("promotion_eligible") is not False):
        raise OrfsPairedUtilityError("ORFS physical observation crosses authority boundary")
    ppa = raw.get("ppa")
    if not isinstance(ppa, Mapping) or not ppa:
        raise OrfsPairedUtilityError("ORFS physical observation PPA is missing")
    if raw.get("report_digest") != _digest(dict(ppa)):
        raise OrfsPairedUtilityError("ORFS physical observation report digest mismatch")
    replay = dict(raw)
    supplied = replay.pop("observation_digest", None)
    if supplied != _digest(replay):
        raise OrfsPairedUtilityError("ORFS physical observation digest mismatch")
    normalized = dict(ppa)
    self_deltas = extract_deltas(normalized, normalized)
    missing = [name for name in ("wns_ns", "tns_ns", "area_um2", "power_w")
               if self_deltas.get(name) is None]
    if missing:
        raise OrfsPairedUtilityError(
            "paired utility observation is incomplete: missing_" +
            ",missing_".join(missing))
    return normalized


def _counterfactual_checks(receipt: CandidateExecutionReceipt) -> dict[str, str]:
    raw = (receipt.metadata.get("oracle_metadata") or {}).get(
        "counterfactual_oracle")
    try:
        checked = replay_counterfactual_oracle_receipt(raw)
    except (TypeError, ValueError) as exc:
        raise OrfsPairedUtilityError(
            "paired utility requires a replayable fixed-constraint oracle") from exc
    if checked.get("complete") is not True:
        raise OrfsPairedUtilityError(
            "paired utility requires a complete fixed-constraint oracle")
    return {
        name: checked["checks"][name]
        for name in ("route", "drc", "lvs", "timing")
    }


def _evaluation_action(candidate: StructuredRepairCandidate,
                       contract: Mapping) -> dict:
    action = copy.deepcopy(candidate.concrete_action)
    payload = action.get("payload")
    if not isinstance(payload, dict):
        raise OrfsPairedUtilityError("paired utility candidate payload is malformed")
    existing = payload.get("utility_contract_id")
    if existing is not None and existing != contract["contract_id"]:
        raise OrfsPairedUtilityError("candidate binds a different utility contract")
    payload["utility_contract_id"] = contract["contract_id"]
    reason = action_contract_binding_reason(action, contract)
    if reason is not None:
        raise OrfsPairedUtilityError(
            f"paired utility action does not match contract: {reason}")
    return action


def _utility_receipt(*, bundle: PairedCandidateExecutionReceipt, arm: str,
                     baseline: CandidateExecutionReceipt,
                     memory: CandidateExecutionReceipt,
                     candidate: StructuredRepairCandidate,
                     contract: Mapping) -> dict:
    action = _evaluation_action(candidate, contract)
    observation = evaluate_observed_contract(
        contract=contract,
        action=action,
        before_ppa=_physical(baseline),
        after_ppa=_physical(memory),
        checks=_counterfactual_checks(memory),
        obligation_coverage=1.0,
    )
    if observation.get("status") == "ABSTAINED":
        raise OrfsPairedUtilityError(
            "paired utility observation is incomplete: " + ",".join(
                observation.get("abstain_reasons") or ["unknown"]))
    payload = {
        "version": ORFS_PAIRED_UTILITY_VERSION,
        "case_id": bundle.case_id,
        "arm": arm,
        "baseline_arm": "NO_MEMORY",
        "baseline_execution_digest": baseline.execution_digest,
        "memory_execution_digest_before_utility": memory.execution_digest,
        "candidate_id": candidate.candidate_id,
        "candidate_digest": candidate.candidate_digest,
        "prior_created_regressions": list(memory.created_regressions),
        "contract_id": contract["contract_id"],
        "contract_digest": utility_contract_digest(contract),
        "observation": observation,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
        "strict_signoff_claim": False,
    }
    payload["receipt_digest"] = _digest(payload)
    return payload


def replay_orfs_paired_utility_receipt(
        baseline: CandidateExecutionReceipt,
        memory: CandidateExecutionReceipt) -> dict:
    """Recompute one embedded utility receipt and its regression projection."""
    raw = memory.metadata.get("paired_utility")
    if not isinstance(raw, Mapping):
        raise OrfsPairedUtilityError("paired utility receipt is missing")
    if raw.get("version") != ORFS_PAIRED_UTILITY_VERSION:
        raise OrfsPairedUtilityError("paired utility receipt version mismatch")
    supplied = raw.get("receipt_digest")
    unsigned = dict(raw)
    unsigned.pop("receipt_digest", None)
    if supplied != _digest(unsigned):
        raise OrfsPairedUtilityError("paired utility receipt digest mismatch")
    contract_id = raw.get("contract_id")
    catalog = known_utility_contracts()
    if contract_id not in catalog:
        raise OrfsPairedUtilityError("paired utility contract is not registered")
    contract = catalog[contract_id]()
    if raw.get("contract_digest") != utility_contract_digest(contract):
        raise OrfsPairedUtilityError("paired utility contract digest mismatch")
    if (raw.get("case_id") != baseline.case_id or
            raw.get("case_id") != memory.case_id or
            raw.get("baseline_arm") != "NO_MEMORY" or
            raw.get("arm") != memory.metadata.get("arm") or
            baseline.source != "no_memory" or memory.source != "structured_memory" or
            raw.get("candidate_id") != memory.candidate_id or
            raw.get("candidate_digest") != memory.candidate_digest or
            raw.get("baseline_execution_digest") != baseline.execution_digest):
        raise OrfsPairedUtilityError("paired utility identity binding mismatch")
    prior = raw.get("prior_created_regressions")
    if (not isinstance(prior, list) or
            any(type(value) is not str or not value for value in prior)):
        raise OrfsPairedUtilityError(
            "paired utility prior regressions are malformed")
    metadata_before = copy.deepcopy(memory.metadata)
    metadata_before.pop("paired_utility", None)
    memory_before = replace(
        memory, created_regressions=tuple(prior), metadata=metadata_before)
    if raw.get("memory_execution_digest_before_utility") != memory_before.execution_digest:
        raise OrfsPairedUtilityError("paired utility memory receipt binding mismatch")
    observation = raw.get("observation")
    action = observation.get("action") if isinstance(observation, Mapping) else None
    if action_contract_binding_reason(action, contract) is not None:
        raise OrfsPairedUtilityError("paired utility action binding mismatch")
    replayed = evaluate_observed_contract(
        contract=contract, action=action,
        before_ppa=_physical(baseline), after_ppa=_physical(memory_before),
        checks=_counterfactual_checks(memory_before), obligation_coverage=1.0)
    if replayed.get("status") == "ABSTAINED":
        raise OrfsPairedUtilityError("paired utility replay is incomplete")
    if observation != replayed:
        raise OrfsPairedUtilityError("paired utility observation replay mismatch")
    expected = set(prior)
    if replayed.get("status") == "FAIL":
        expected.update(
            f"utility_contract:{contract_id}:{failure}"
            for failure in replayed.get("failures") or [])
    if tuple(sorted(expected)) != memory.created_regressions:
        raise OrfsPairedUtilityError("paired utility regression projection mismatch")
    if (raw.get("evaluation_only") is not True or
            raw.get("canonical_memory_mutation") != "none" or
            raw.get("promotion_eligible") is not False or
            raw.get("strict_signoff_claim") is not False):
        raise OrfsPairedUtilityError("paired utility crosses authority boundary")
    return dict(raw)


def apply_orfs_paired_utility_contract(
        bundle: PairedCandidateExecutionReceipt,
        arm_candidates: Mapping[str, StructuredRepairCandidate | None],
        *, contract: Mapping) -> PairedCandidateExecutionReceipt:
    """Annotate memory arms with contract-derived regression witnesses.

    A contract violation is represented by deterministic
    ``utility_contract:<id>:<failure>`` entries.  Missing or incomplete PPA is
    fail-closed: the function raises and the campaign produces no apparently
    complete P12 cohort.  Policy fallback arms remain no-memory observations
    and are never attributed to a candidate that was not executed.
    """
    if not isinstance(bundle, PairedCandidateExecutionReceipt):
        raise OrfsPairedUtilityError("paired utility bundle is invalid")
    if not isinstance(arm_candidates, Mapping) or set(arm_candidates) != set(P12_ARMS):
        raise OrfsPairedUtilityError("paired utility candidates must cover all P12 arms")
    validate_utility_contract(contract)
    baseline = bundle.arm_receipts["NO_MEMORY"]
    if baseline.source != "no_memory" or baseline.outcome not in _POSITIVE_OUTCOMES:
        raise OrfsPairedUtilityError(
            "paired utility requires a successful no-memory baseline")
    # Validate the baseline before constructing any replacement receipt.
    _physical(baseline)
    _counterfactual_checks(baseline)

    receipts = dict(bundle.arm_receipts)
    for arm in _MEMORY_ARMS:
        memory = receipts[arm]
        if memory.source == "no_memory":
            continue
        candidate = arm_candidates[arm]
        if not isinstance(candidate, StructuredRepairCandidate):
            raise OrfsPairedUtilityError(
                f"paired utility {arm} lacks its executed candidate")
        if (memory.candidate_id != candidate.candidate_id or
                memory.candidate_digest != candidate.candidate_digest):
            raise OrfsPairedUtilityError(
                f"paired utility {arm} candidate identity mismatch")
        if memory.outcome not in _POSITIVE_OUTCOMES:
            # Flow/signoff harm already has direct oracle authority.  Do not
            # manufacture a physical classification from an incomplete run.
            continue
        utility = _utility_receipt(
            bundle=bundle, arm=arm, baseline=baseline, memory=memory,
            candidate=candidate, contract=contract)
        failures = (utility["observation"].get("failures") or [])
        regressions = set(memory.created_regressions)
        if utility["observation"].get("status") == "FAIL":
            regressions.update(
                f"utility_contract:{contract['contract_id']}:{failure}"
                for failure in failures)
        metadata = copy.deepcopy(memory.metadata)
        metadata["paired_utility"] = utility
        receipts[arm] = replace(
            memory,
            created_regressions=tuple(sorted(regressions)),
            metadata=metadata,
        )
    return replace(bundle, arm_receipts=receipts)


__all__ = [
    "ORFS_PAIRED_UTILITY_VERSION", "OrfsPairedUtilityError",
    "apply_orfs_paired_utility_contract", "replay_orfs_paired_utility_receipt",
]
