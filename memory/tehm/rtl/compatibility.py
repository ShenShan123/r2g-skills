"""Explicit RTL compatibility profiles (Phase-10 structural admission).

The profile is deliberately coarser than a concrete state/signal name.  It is
the contract used to decide whether two parser-backed actions share the same
structural execution family.  A missing or mismatched profile is fail-closed at
retrieval/activation; it is never inferred from a convenient target binding.
"""
from __future__ import annotations

import re

COMPATIBILITY_VERSION = "rtl-compatibility-v1"
_PROFILE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{2,127}$")

DEFAULT_PROFILES = {
    "rtl.GUARD_STRENGTHEN": "rtl.fsm.single_guard.v1",
    "rtl.RESET_RESTORE": "rtl.sequential.reset_branch.v1",
    "rtl.WIDTH_CORRECT": "rtl.combinational.width_assignment.v1",
    "rtl.PRIORITY_REORDER": "rtl.fsm.case_reorder.v1",
    "rtl.AST_REWRITE": "rtl.ast.literal_rewrite.v1",
}


def profile_for_action(payload: dict) -> str:
    """Return a validated explicit profile for one RTL action payload."""
    domain = str(payload.get("domain") or "")
    profile = payload.get("compatibility_profile") or DEFAULT_PROFILES.get(domain)
    if not isinstance(profile, str) or not _PROFILE_RE.fullmatch(profile):
        raise ValueError(
            "RTL action requires compatibility_profile matching "
            "[A-Za-z][A-Za-z0-9_.:-]{2,127}")
    return profile


def annotate_graph(graph, profile: str) -> None:
    """Attach the profile as a typed graph fact without changing RTL nodes."""
    if not isinstance(profile, str) or not _PROFILE_RE.fullmatch(profile):
        raise ValueError(f"invalid RTL compatibility profile: {profile!r}")
    graph.add_node(
        f"compatibility:{profile}", "COMPATIBILITY_PROFILE", label=profile,
        attrs={"version": COMPATIBILITY_VERSION})


def profile_from_graph(graph: dict | None) -> str | None:
    """Read the unique compatibility profile carried by a graph artifact."""
    profiles = sorted({str(node.get("label")) for node in (graph or {}).get("nodes", [])
                       if isinstance(node, dict) and
                       node.get("kind") == "COMPATIBILITY_PROFILE"})
    if len(profiles) == 1:
        return profiles[0]
    return None


def structural_compatibility(source: dict | None, candidate: dict | None) -> dict:
    """Classify structural evidence for negative/profile-bound examples.

    ``INAPPLICABLE`` is reserved for a concrete profile mismatch.  Missing
    module/case/graph facts are ``UNRESOLVED`` rather than an optimistic pass.
    The result is suitable for retrieval and activation receipts and does not
    replace the executable equivalence oracle.
    """
    source = source if isinstance(source, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    source_profile = source.get("compatibility_profile")
    candidate_profile = candidate.get("compatibility_profile")
    if not source_profile or not candidate_profile:
        return {"status": "UNRESOLVED", "reason": "missing_compatibility_profile"}
    if source_profile != candidate_profile:
        return {"status": "INAPPLICABLE", "reason": "compatibility_profile_mismatch",
                "source_profile": source_profile, "candidate_profile": candidate_profile}
    required = ("module", "case_expr")
    missing = [key for key in required
               if not source.get(key) or not candidate.get(key)]
    if missing:
        return {"status": "UNRESOLVED", "reason": "missing_structural_fact",
                "missing": missing}
    mismatched = [key for key in required if source.get(key) != candidate.get(key)]
    if mismatched:
        return {"status": "UNRESOLVED", "reason": "structural_context_mismatch",
                "mismatched": mismatched}
    return {"status": "APPLICABLE", "reason": "profile_and_structure_match"}
