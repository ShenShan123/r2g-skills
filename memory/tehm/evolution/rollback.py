"""Rollback receipts for isolated online candidate trials.

The online candidate lane operates on an in-memory SQLite copy.  Closing that
copy is intentionally different from proving a real RTL/ORFS source-tree
rollback; this module records the narrower fact explicitly so callers cannot
silently reuse it as production rollback authority.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IsolatedRollbackReceipt:
    source_digest_before: str
    source_digest_after: str
    staging_digest_before: str
    staging_digest_after: str
    staging_discarded: bool
    verified: bool
    authority: str = "isolated_staging_discard"
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "version": "isolated-rollback-receipt-v1",
            "authority": self.authority,
            "source_digest_before": self.source_digest_before,
            "source_digest_after": self.source_digest_after,
            "staging_digest_before": self.staging_digest_before,
            "staging_digest_after": self.staging_digest_after,
            "staging_discarded": self.staging_discarded,
            "verified": self.verified,
            "reason": self.reason,
        }


def build_isolated_rollback_receipt(
    *,
    source_digest_before: str,
    source_digest_after: str,
    staging_digest_before: str,
    staging_digest_after: str,
    staging_discarded: bool = True,
) -> IsolatedRollbackReceipt:
    """Build a fail-closed receipt for the in-memory staging boundary."""
    values = {
        "source_digest_before": source_digest_before,
        "source_digest_after": source_digest_after,
        "staging_digest_before": staging_digest_before,
        "staging_digest_after": staging_digest_after,
    }
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise ValueError("isolated rollback digests are required")
    source_restored = source_digest_before == source_digest_after
    verified = bool(source_restored and staging_discarded)
    if verified:
        reason = "source_digest_unchanged_and_staging_discarded"
    elif not source_restored:
        reason = "source_digest_changed"
    else:
        reason = "staging_discard_not_verified"
    return IsolatedRollbackReceipt(
        source_digest_before=source_digest_before,
        source_digest_after=source_digest_after,
        staging_digest_before=staging_digest_before,
        staging_digest_after=staging_digest_after,
        staging_discarded=bool(staging_discarded),
        verified=verified,
        reason=reason,
    )


__all__ = ["IsolatedRollbackReceipt", "build_isolated_rollback_receipt"]
