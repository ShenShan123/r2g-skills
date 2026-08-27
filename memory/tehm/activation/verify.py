"""Step 7: oracle verification (design doc 10, 12).

Runs the available oracles over the execution evidence and produces a
VerifierSnapshot-shaped verdict. The oracle callable returns a dict with at
least ``verdict`` / ``oracle_type``; created regressions and newly observed
failures are distinguished (design doc 8).
"""
from __future__ import annotations

from typing import Callable

VERIFY_VERSION = "verify-v0.1"


def verify_execution(execution: dict | None, obligations: list, *,
                     oracle: Callable | None = None) -> dict:
    """Step 7 result. Without an oracle the verdict is honestly UNKNOWN.

    An executor may embed its own verification result in the execution evidence
    (``execution['verification']``) — this is the RTL case, where executing a
    source change IS compiling + simulating it.
    """
    if execution is None:
        return _unknown("no_execution")
    embedded = execution.get("verification")
    if isinstance(embedded, dict) and embedded.get("verdict") in ("PASS", "FAIL"):
        result = dict(embedded)
        result.setdefault("created_regressions", [])
        result.setdefault("newly_observed_failures", [])
        return result
    if oracle is None:
        return _unknown("no_oracle_wired")
    result = dict(oracle(execution, obligations))
    result.setdefault("created_regressions", [])
    result.setdefault("newly_observed_failures", [])
    return result


def _unknown(reason: str) -> dict:
    return {
        "verdict": "UNKNOWN",
        "oracle_type": "UNKNOWN",
        "scope": reason,
        "confidence_tier": "H",
        "obligation_coverage": None,
        "evidence_refs": [],
        "created_regressions": [],
        "newly_observed_failures": [],
    }
