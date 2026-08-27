"""Content-addressed ID minting for TEHM.

Every TEHM object carries a deterministic content-addressed ID: identical
content yields the identical ID (dedup + idempotent re-ingest), and any content
change yields a different ID. Timestamps and other volatile metadata are NEVER
part of the digest (design doc 27.3 H1/H4, test ``test_state_id_content_addressed``).

The digest is a sha1 over the canonical, byte-stable JSON of the content
fields. ``stable_dumps`` is the one JSON serializer used everywhere so all
digests agree.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_dumps(obj: Any) -> str:
    """Byte-stable JSON: sorted keys, compact separators (no trailing space)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def is_hole(value) -> bool:
    """True when ``value`` is an anti-unification hole (``$H<n>``)."""
    return isinstance(value, str) and value.startswith("$H")


def _content_digest(*parts: Any, prefix: str) -> str:
    payload = stable_dumps([p for p in parts])
    return f"{prefix}_{hashlib.sha1(payload.encode()).hexdigest()[:16]}"


def state_id(*, domain: str, source_digest: str, context_graph_digest: str,
             verifier_snapshot: dict, artifact_manifest: dict) -> str:
    return _content_digest(
        {"domain": domain, "src": source_digest, "ctx": context_graph_digest,
         "verifier": verifier_snapshot, "manifest": artifact_manifest},
        prefix="state",
    )


def transition_id(*, source_state_id: str, target_state_id: str, action: dict,
                  observation_delta: dict, verifier: dict) -> str:
    return _content_digest(
        {"src": source_state_id, "dst": target_state_id, "action": action,
         "delta": observation_delta, "verifier": verifier},
        prefix="transition",
    )


def episode_id(*, domain: str, initial_state_id: str, mechanism_family: str | None,
               lineage_id: str | None, ordered_transition_ids: list[str]) -> str:
    return _content_digest(
        {"domain": domain, "init": initial_state_id, "family": mechanism_family,
         "lineage": lineage_id, "steps": ordered_transition_ids},
        prefix="episode",
    )


def rule_id(*, domain: str, before_pattern: dict, after_pattern: dict,
            hard_preconditions: list, obligations: list) -> str:
    return _content_digest(
        {"domain": domain, "before": before_pattern, "after": after_pattern,
         "pre": hard_preconditions, "oblig": obligations},
        prefix="rule",
    )


def activation_id(*, rule_id_: str, target_state_id: str, retrieval_receipt: dict) -> str:
    return _content_digest(
        {"rule": rule_id_, "target": target_state_id, "retrieval": retrieval_receipt},
        prefix="activation",
    )
