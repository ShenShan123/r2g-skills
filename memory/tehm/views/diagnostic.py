"""Diagnostic view: the failure signature ``F`` (design doc 22.2).

``F = <trigger, first_divergence, temporal_signature, observable_symptom,
       structural_locus>`` — derived from counterexamples, waveforms, assertion
traces, differential sim and causal cones (RTL); for the flow/signoff v1 domain
we derive the same 5-tuple from reports + failure signature evidence.
"""
from __future__ import annotations

import sqlite3

from tehm import SCHEMA_VERSION
from tehm.views.base import ViewRecord, upsert_view

DIAGNOSTIC_EXTRACTOR_VERSION = "diagnostic-v0.1"


def extract_diagnostic_signature(state_content: dict) -> dict:
    """Build ``F`` from a state's reports + failure signature evidence.

    Never fabricates: fields we cannot observe stay ``UNKNOWN`` (H3), and the
    scope string records what evidence was actually read.
    """
    reports = dict(state_content.get("reports") or {})
    failure_sig = dict(state_content.get("failure_signature") or {})

    # observable symptom: dominant failing check + class + count.
    symptom_parts: dict = {"check": None, "class": None, "count": None}
    for key in ("drc", "lvs", "timing", "route"):
        report = reports.get(key) or {}
        status = str(report.get("status", ""))
        total = report.get("total_violations")
        if status not in ("", "clean", "clean_beol", "complete", "skipped"):
            symptom_parts["check"] = key
            categories = report.get("categories") or {}
            if isinstance(categories, dict) and categories:
                dominant = max(categories, key=lambda c: (categories[c].get("count") or 0))
                symptom_parts["class"] = dominant
                symptom_parts["count"] = categories[dominant].get("count")
            elif total is not None:
                symptom_parts["count"] = total
            break

    first_divergence = failure_sig.get("first_divergence") or \
        (reports.get("timing") or {}).get("first_divergence")

    return {
        "trigger": failure_sig.get("trigger"),
        "first_divergence": first_divergence,
        "temporal_signature": failure_sig.get("temporal_signature", "UNKNOWN"),
        "observable_symptom": symptom_parts,
        "structural_locus": failure_sig.get("structural_locus")
            or symptom_parts.get("class"),
        "source_scope": "flow/signoff-v1",
    }


def build_diagnostic_view(owner_type: str, owner_id: str, signature: dict,
                          *, source_refs: list[str] | None = None,
                          materialized_at: str = "") -> ViewRecord:
    return ViewRecord(
        owner_type=owner_type,
        owner_id=owner_id,
        view_type="diagnostic",
        schema_version=SCHEMA_VERSION,
        extractor_version=DIAGNOSTIC_EXTRACTOR_VERSION,
        payload={"failure_signature": signature},
        source_refs=list(source_refs or []),
        materialized_at=materialized_at,
    )


def materialize_diagnostic(conn: sqlite3.Connection, owner_type: str, owner_id: str,
                           signature: dict, *, source_refs: list[str] | None = None,
                           materialized_at: str = "", commit: bool = True) -> ViewRecord:
    record = build_diagnostic_view(owner_type, owner_id, signature,
                                   source_refs=source_refs,
                                   materialized_at=materialized_at)
    upsert_view(conn, record, commit=commit)
    return record
