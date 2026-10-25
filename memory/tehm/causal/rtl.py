"""Real RTL execution -> canonical capture -> causal shadow receipt."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from dataclasses import dataclass

from tehm.canonical.capture import CaptureReceipt, capture
from tehm.rtl.rtl_evidence import build_rtl_execution_record
from tehm.rtl.rtl_oracle import IcarusOracle

from .path_builder import build_transition_causal_fragment
from .receipts import CausalFragment


@dataclass(frozen=True)
class RtlCausalReceipt:
    capture: CaptureReceipt
    fragment: CausalFragment
    verifier: dict

    def to_dict(self) -> dict:
        return {
            "capture": self.capture.to_dict(),
            "fragment": self.fragment.to_dict(),
            "verifier": self.verifier,
        }


def capture_rtl_causal_fragment(
    conn: sqlite3.Connection,
    store,
    project: Path | str,
    *,
    oracle: IcarusOracle | None = None,
    campaign_id: str = "live",
    dataset_split: str = "training",
    dataset_learner_eligible: bool = True,
) -> RtlCausalReceipt:
    """Capture one parser-backed RTL fix and immediately build its shadow fragment.

    The caller must explicitly choose campaign membership.  The helper does not
    promote a rule, asset, causal path, or capability.
    """
    record = build_rtl_execution_record(Path(project), oracle=oracle, store=store)
    capture_receipt = capture(
        conn, store, record, dataset_campaign_id=campaign_id,
        dataset_split=dataset_split,
        dataset_learner_eligible=dataset_learner_eligible)
    fragment = build_transition_causal_fragment(
        conn, capture_receipt.transition_id, campaign_id=campaign_id)
    return RtlCausalReceipt(
        capture=capture_receipt, fragment=fragment,
        verifier=record.verification)


__all__ = ["RtlCausalReceipt", "capture_rtl_causal_fragment"]
