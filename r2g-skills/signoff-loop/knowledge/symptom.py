#!/usr/bin/env python3
"""Canonical symptom signature for the symptom-indexed memory (spec 2026-06-09).

A symptom is {check, class, predicates} -> a stable symptom_id hash. The symptom
is the UNIVERSAL index for learned repair experience; design-family/name is never
part of it. Pure module: no I/O, no DB, fully unit-testable.
"""
from __future__ import annotations

import hashlib
import json

SYMPTOM_SCHEMA_VERSION = 1

# Curated, decision-relevant predicate keys per check. ONLY these participate in
# the symptom_id hash. Kept deliberately small so symptoms pool (don't fragment).
_PREDICATE_KEYS: dict[str, tuple[str, ...]] = {
    "lvs": ("nets_balanced", "device_mismatch_present", "same_cell_swap_present",
            "sigsegv", "internal_assertion", "extraction_terminated"),
    "drc": ("beol_only",),
    "timing": ("single_dominant_path",),
    "synth": ("post_ast_marker_ge_3",),
    "orfs_stage": (),
}


def normalize_class(vclass: str | None) -> str | None:
    """Normalize a violation-class token before it enters a signature.

    KLayout XML <category> names arrive wrapped in LITERAL quotes ("'m3.2'",
    "'M4.S.5'") and sometimes as full rule prose ("'RULE : description : 15nm'");
    stored verbatim they fragment the symptom index into single-use buckets that
    can never pool repair experience (2026-07-04 audit: 7+ quoted classes, one a
    100-char LISD spacing sentence, plus a quoted-whitespace class). Strip
    wrapping quotes/whitespace, keep only the leading rule token of a
    ' : '-separated description, and collapse empty to None."""
    if vclass is None:
        return None
    c = str(vclass).strip()
    while len(c) >= 2 and c[0] == c[-1] and c[0] in ("'", '"'):
        c = c[1:-1].strip()
    if " : " in c:
        c = c.split(" : ", 1)[0].strip()
    return c or None


def canonical_signature(check: str | None, vclass: str | None,
                        predicates: dict | None = None) -> dict:
    """Canonical {check, class, predicates} with the class normalized
    (normalize_class) and predicates filtered to the curated, TRUE-valued
    decision keys for this check (sparse, true-only)."""
    preds: dict[str, bool] = {}
    for k in _PREDICATE_KEYS.get(check or "", ()):
        if (predicates or {}).get(k):
            preds[k] = True
    return {"check": check, "class": normalize_class(vclass), "predicates": preds}


def symptom_id(signature: dict) -> str:
    """Stable 16-hex hash over (check, class, sorted true predicate keys)."""
    payload = json.dumps(
        [signature.get("check"), signature.get("class"),
         sorted((signature.get("predicates") or {}).keys())],
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def predicates_for(check: str | None, report: dict) -> dict:
    """Derive curated booleans from a parsed reports/<check>.json dict. Missing
    fields -> predicate simply absent (yields a coarser, still-valid symptom)."""
    p: dict[str, bool] = {}
    if check == "lvs":
        so = report.get("net_mismatches_schematic_only")
        lo = report.get("net_mismatches_layout_only")
        if so is not None and lo is not None:
            p["nets_balanced"] = (so == lo)
        if (report.get("device_mismatches") or 0) > 0:
            p["device_mismatch_present"] = True
        if (report.get("circuit_swaps") or 0) > 0:
            p["same_cell_swap_present"] = True
        crash_line = (report.get("crash_line") or "").lower()
        if report.get("crash") and any(t in crash_line for t in
                                       ("sigsegv", "signal", "sort_circuit")):
            p["sigsegv"] = True
        if report.get("status") == "incomplete" and "assert" in crash_line:
            p["internal_assertion"] = True
    elif check == "drc":
        if str(report.get("drc_mode") or "").startswith("beol"):
            p["beol_only"] = True
    return p


def from_fix_log_row(row: dict) -> tuple[dict, str]:
    """Build (signature, symptom_id) from a fix_log.jsonl row. Uses row['check'],
    row['violation_class'], and the optional row['predicates'] dict (absent on
    backfilled/legacy rows -> coarse class-only signature)."""
    sig = canonical_signature(row.get("check"), row.get("violation_class"),
                              row.get("predicates"))
    return sig, symptom_id(sig)
