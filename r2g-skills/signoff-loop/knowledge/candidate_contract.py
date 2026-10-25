#!/usr/bin/env python3
"""One executor contract for every repair candidate (catalogue, TEHM, SOURCE recipe).

failure-patterns "Memory candidate broke the executor contract, and the failure was
suppressed" (2026-10-17): RECIPE candidates lacked ``recheck``; ``diagnose --next`` raised
KeyError; ``fix_signoff`` read the empty output as an ordinary stop. A candidate that does
not satisfy this contract is a TECHNICAL failure: it is never executed and never scored as
an ordinary repair failure. Required values are mapped from recorded evidence only; there
is no default that could hide a missing field.
"""
from __future__ import annotations

FIX_CHECKS = ("drc", "lvs", "route", "timing")              # fix_signoff.sh --check values
RERUN_STAGES = ("synth", "floorplan", "place", "cts", "route")
# Evidence/situation check vocabulary -> the fix loop's check (a backend abort is keyed
# check=orfs_stage in the situation index and repaired by --check route).
EVIDENCE_TO_FIX = {"drc": "drc", "lvs": "lvs", "route": "route", "orfs_stage": "route",
                   "timing": "timing"}
TECHNICAL_EXIT = 4                                        # fix_signoff.sh exit on a technical failure
# Which fields must EXIST on every candidate (review 2026-10-17: the first version checked
# types only when a field was present, so {"id": "x", "recheck": "drc"} passed). Every
# catalogue strategy, TEHM rule and SOURCE recipe already emits all four.
#   id            non-empty single-line string
#   recheck       one of FIX_CHECKS (mapped from recorded evidence, never defaulted)
#   rerun_from    one of RERUN_STAGES, or None/"" ONLY for a recheck-only candidate with
#                 no edits (lvs_resolve_unknown, lvs_macro_cdl): an edit with no rerun
#                 would be "verified" against stale results
#   config_edits  a mapping; may be EMPTY (the action is carried elsewhere)
# Optional: sdc_edits (a mapping, may be empty, when present).
REQUIRED_FIELDS = ("id", "recheck", "rerun_from", "config_edits")
OPTIONAL_FIELDS = ("sdc_edits",)


def recheck_from_evidence(evidence_check) -> str | None:
    """The fix-loop recheck for a recorded evidence check, or None when it cannot be mapped."""
    return EVIDENCE_TO_FIX.get(evidence_check) if isinstance(evidence_check, str) else None


def contract_errors(strategy) -> list[str]:
    """Every violation of the executor contract (empty list = executable)."""
    if not isinstance(strategy, dict):
        return ["candidate is not a mapping"]
    errors = ["%s missing" % f for f in REQUIRED_FIELDS if f not in strategy]
    sid = strategy.get("id")
    if "id" in strategy and (not isinstance(sid, str) or not sid.strip()
                             or any(c in sid for c in "\t\n")):
        errors.append("id is not a non-empty single-line string")
    if "recheck" in strategy and strategy["recheck"] not in FIX_CHECKS:
        errors.append("recheck %r not one of %s" % (strategy["recheck"], list(FIX_CHECKS)))
    edits = strategy.get("config_edits")
    if "config_edits" in strategy and not isinstance(edits, dict):
        errors.append("config_edits is not a mapping")
    sdc = strategy.get("sdc_edits")
    if "sdc_edits" in strategy and not isinstance(sdc, dict):
        errors.append("sdc_edits is not a mapping")
    if "rerun_from" in strategy:
        rerun = strategy["rerun_from"]
        if rerun in (None, ""):
            if (isinstance(edits, dict) and edits) or (isinstance(sdc, dict) and sdc):
                errors.append("rerun_from empty but the candidate edits config/SDC")
        elif rerun not in RERUN_STAGES:
            errors.append("rerun_from %r not one of %s" % (rerun, list(RERUN_STAGES)))
    return errors


def describe(invalid: list) -> str:
    """One-line, tab-free summary of invalid candidates for the --next protocol."""
    parts = ["%s: %s" % (i.get("id"), "; ".join(i.get("errors") or [])) for i in invalid]
    return " | ".join(parts).replace("\t", " ").replace("\n", " ")[:900]
