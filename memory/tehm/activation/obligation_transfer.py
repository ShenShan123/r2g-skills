"""Step 4: obligation transfer check (design doc 10, 25).

For each required obligation q in Q:
    BOUND         the oracle for q is available in the current environment
    SYNTHESIZABLE the oracle could be produced (e.g. a target test from the
                  project's own testbench)
    UNAVAILABLE   no oracle, and it cannot be synthesized

``OC = #checked / #required``. An unavailable obligation must lower activation
confidence — never silently pass (design doc 25, honesty H7).
"""
from __future__ import annotations

from contracts import RepairContext

OBLIGATION_VERSION = "obligation-transfer-v0.1"
OBLIGATION_RESULTS = ("BOUND", "SYNTHESIZABLE", "UNAVAILABLE", "PASS", "FAIL")


def transfer_obligations(rule: dict, context: RepairContext, *,
                         oracle_registry: set | None = None) -> dict:
    """Evaluate every rule obligation against the available oracle set.

    ``oracle_registry``: names of oracles available in the environment (e.g.
    ``{"drc", "lvs", "timing", "route", "rcx"}``). Defaults to the signoff
    reports present in the context.
    """
    obligations = sorted(set(rule.get("obligations") or []))
    if not obligations:
        return {"results": [], "obligation_coverage": 0.0,
                "oracle_registry": sorted(oracle_registry or {})}

    registry = oracle_registry if oracle_registry is not None else \
        {name for name in ("drc", "lvs", "timing", "route", "rcx")
         if (context.reports or {}).get(name)}

    results = []
    for obligation in obligations:
        oracle = _oracle_for(
            obligation, target_check=getattr(context, "check", None))
        if oracle in registry:
            status = "BOUND"
            evidence_refs = [f"report:{oracle}"]
        elif context.reports or context.cfg:
            status = "SYNTHESIZABLE"   # a project-local testbench/flow exists
            evidence_refs = []
        else:
            status = "UNAVAILABLE"
            evidence_refs = []
        results.append({"obligation": obligation, "status": status,
                        "oracle": oracle,
                        "evidence_refs": evidence_refs})

    # ``SYNTHESIZABLE`` is a plan, not evidence.  It must never contribute to
    # checked obligation coverage or promotion authority.
    checked = sum(1 for r in results if r["status"] == "BOUND")
    return {
        "results": results,
        "obligation_coverage": checked / len(results),
        "oracle_registry": sorted(registry),
    }


def finalize_obligations(transfer: dict, verifier: dict) -> dict:
    """Attach per-obligation verifier evidence after execution.

    A transfer describes what was available before execution.  A PASS/FAIL
    oracle result can upgrade that plan only when it reports complete coverage;
    otherwise the original UNAVAILABLE/SYNTHESIZABLE state remains visible.
    """
    results = [dict(item) for item in (transfer.get("results") or [])]
    if not results:
        return dict(transfer)
    coverage = verifier.get("obligation_coverage")
    refs = list(verifier.get("evidence_refs") or [])
    if coverage is not None and float(coverage) >= 1.0 and \
            verifier.get("verdict") in {"PASS", "FAIL"}:
        final_status = verifier["verdict"]
        for item in results:
            item["status"] = final_status
            item["evidence_refs"] = refs
            item["verifier"] = verifier.get("oracle_type", "UNKNOWN")
        return {
            **transfer, "results": results,
            "obligation_coverage": 1.0,
            "finalized_by_verifier": True,
        }
    return {**transfer, "results": results, "finalized_by_verifier": False}


def _oracle_for(obligation: str, *, target_check: str | None = None) -> str:
    """Map an obligation to the oracle that can check it (design doc 25)."""
    lowered = obligation.lower()
    # Crystallization emits the typed verifier obligation
    # ``VERIFIER_TARGET_TEST`` for flow/signoff target observations.  It is
    # still the current target-check oracle; treating it as an empty oracle
    # silently made every real ORFS activation report 0 coverage.
    if (obligation in {"TARGET_FAILURE_REMOVED", "REQUIRED_STAGE_COMPLETED"}
            or "target_test" in lowered or lowered == "verifier_target_test"):
        return str(target_check or "").lower()
    if lowered.startswith("rtl_") or "assertion" in lowered or \
            "handshake_property" in lowered or "reset_semantics" in lowered:
        return "rtl"
    for name in ("drc", "lvs", "timing", "route", "rcx"):
        if name in lowered or name + "_" in obligation.lower():
            return name
    return ""
