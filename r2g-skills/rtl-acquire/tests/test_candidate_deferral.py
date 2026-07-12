"""Low-priority deferral for risk-flagged candidates (codex #1) and the
resource-guard scoping fix (failure-patterns.md #38).

- expand_candidates defers risk-flagged / resource_tier=high candidates to the
  TAIL of the round (a low-priority queue) instead of running them in CSV order
  where a memory-heavy design blocks the clean ones. Risk is DEFERRED, never
  dropped — the synth attempt is still the arbiter.
- run_expansion_round's high-mem round guard now only counts candidates that
  would ACTUALLY run this round (pass the --priorities filter), so a high-mem
  row filtered out by priority never hard-blocks a round it was never in.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from execute.expand_candidates import _candidate_is_risky  # noqa: E402
from run_expansion_round import runnable_high_mem_designs  # noqa: E402


class RiskFlagTests(unittest.TestCase):
    def test_risk_flags_token_is_risky(self):
        self.assertTrue(_candidate_is_risky(
            {"notes": "source=foo|risk_flags=sram+blackbox"}))

    def test_risk_flags_none_is_not_risky(self):
        self.assertFalse(_candidate_is_risky({"notes": "risk_flags=none"}))

    def test_resource_tier_high_is_risky(self):
        self.assertTrue(_candidate_is_risky({"notes": "", "resource_tier": "HIGH"}))

    def test_clean_candidate_is_not_risky(self):
        self.assertFalse(_candidate_is_risky(
            {"notes": "just a normal note", "resource_tier": "medium"}))


class DeferralSortTests(unittest.TestCase):
    def test_risky_candidates_sorted_to_tail_stably(self):
        rows = [
            {"design": "clean_a", "notes": "risk_flags=none"},
            {"design": "risky_1", "notes": "risk_flags=sram"},
            {"design": "clean_b", "notes": ""},
            {"design": "risky_2", "notes": "x", "resource_tier": "high"},
            {"design": "clean_c", "notes": "risk_flags=none"},
        ]
        # same stable key the expander applies
        rows.sort(key=lambda r: 1 if _candidate_is_risky(r) else 0)
        order = [r["design"] for r in rows]
        # clean ones keep CSV order and precede the risky ones (also CSV order)
        self.assertEqual(order, ["clean_a", "clean_b", "clean_c", "risky_1", "risky_2"])


class ResourceGuardScopeTests(unittest.TestCase):
    def test_high_mem_filtered_out_by_priority_does_not_block(self):
        """A resource_tier=high row with priority=low must NOT block a
        --priorities high round (the over-broad guard bug)."""
        rows = [
            {"design": "big", "priority": "low", "resource_tier": "high"},
            {"design": "small", "priority": "high", "resource_tier": "medium"},
        ]
        self.assertEqual(runnable_high_mem_designs(rows, ["high"]), [])

    def test_high_mem_in_scope_still_blocks(self):
        rows = [
            {"design": "big", "priority": "high", "resource_tier": "high"},
            {"design": "small", "priority": "high", "resource_tier": "medium"},
        ]
        self.assertEqual(runnable_high_mem_designs(rows, ["high"]), ["big"])

    def test_no_priority_filter_counts_all(self):
        rows = [{"design": "big", "priority": "low", "resource_tier": "high"}]
        self.assertEqual(runnable_high_mem_designs(rows, []), ["big"])


if __name__ == "__main__":
    unittest.main()
