#!/usr/bin/env python3
"""Action-domain contract for learnable strategies (RMD-HO-P1-02, held-out V3).

Every learnable strategy belongs to exactly one PIPELINE STAGE, and only
signoff-domain strategies may enter the signoff Recipe lifecycle and the
physical A/B harness.

The defect this closes: `project_frontend_diagnosis` wrote `acquire_exclude`
into the signoff repair ledger, `ingest_run` projected it into `fix_events`, the
learner made it a symptom-keyed recipe, and `engineer_loop._known_apply_strategy`
admitted it because its fix_events fallback accepts ANY strategy carrying a
non-empty historical verdict. `plan_arms_for_candidates` has platform scoping but
no action-domain filter, so it planned PHYSICAL A/B arms over synth-only
acquisition workspaces: four inconclusive trials per platform per cohort, and an
unapplyable candidate kept alive forever.

    A historical event proves an action was RECORDED. It does not prove the
    current executor has a handler for it, nor that the subject belongs to the
    same pipeline stage.

Design notes:

  * Domain is derived from the strategy NAME (an explicit prefix registry) and
    corroborated by the `check_type` of the evidence it rides on. Both are cheap,
    both are stable, and neither needs a schema migration of the shipped store.
  * The fix_events fallback in `_known_apply_strategy` is NOT removed — it exists
    so a stale static catalog can never mis-park a genuinely-learned recipe
    (P0-6, 2026-07-15). It is instead NARROWED: only evidence that is itself
    signoff-domain can vouch for a strategy. That keeps both guarantees.
  * ACQUISITION domain is fail-closed by name; UNKNOWN domain falls back to the
    evidence check rather than a blanket reject, so a future signoff strategy
    added to the catalog but not to this registry still validates normally.
"""
from __future__ import annotations

SIGNOFF = "signoff"
ACQUISITION = "acquisition"
GRAPH = "graph"
UNKNOWN = "unknown"

# Strategy-name prefixes owned by a NON-signoff stage. `acquire_*` is written by
# rtl-acquire's frontend diagnosis; `rtl_*` / `acquisition_*` are reserved so a
# later acquisition action cannot leak by picking a new verb.
_DOMAIN_PREFIXES: tuple[tuple[str, str], ...] = (
    ("acquire_", ACQUISITION),
    ("acquisition_", ACQUISITION),
    ("rtl_acquire_", ACQUISITION),
    ("graph_", GRAPH),
    ("dataset_", GRAPH),
)

# `check_type` values a SIGNOFF fix_event can legitimately carry. Note `synth` is
# absent on purpose: acquisition frontend rows use check="synth", while the real
# backend synth-abort recovery (synth_memory_relax) rides check_type='orfs_stage'
# with violation_class='synth'. That asymmetry is what makes the evidence test
# discriminating rather than decorative.
SIGNOFF_CHECK_TYPES = frozenset({"drc", "lvs", "timing", "route", "orfs", "orfs_stage"})


def domain_of(strategy: str | None) -> str:
    """Pipeline stage that owns `strategy`, from its name alone."""
    name = (strategy or "").strip().lower()
    if not name:
        return UNKNOWN
    for prefix, dom in _DOMAIN_PREFIXES:
        if name.startswith(prefix):
            return dom
    return UNKNOWN


def is_signoff_domain(strategy: str | None) -> bool:
    """False only when the NAME proves another stage owns it (fail-closed there,
    fail-open for unknown names so the evidence check decides)."""
    return domain_of(strategy) in (SIGNOFF, UNKNOWN)


def has_signoff_evidence(conn, strategy: str | None) -> bool:
    """True iff `strategy` has at least one fix_event that is itself signoff-domain
    (a signoff check_type AND a real verdict).

    Fails SAFE (True) on any DB error: a transient read must never mis-park a real
    recipe — the name-based guard above is the fail-closed half.
    """
    if not strategy or conn is None:
        return True
    placeholders = ",".join("?" for _ in SIGNOFF_CHECK_TYPES)
    try:
        row = conn.execute(
            f"SELECT 1 FROM fix_events WHERE strategy=? "
            f"AND COALESCE(verdict,'') NOT IN ('', 'none') "
            f"AND COALESCE(check_type,'') IN ({placeholders}) LIMIT 1",
            (strategy, *sorted(SIGNOFF_CHECK_TYPES))).fetchone()
        return row is not None
    except Exception:
        return True
