import unittest
from replay import STRATEGY, effect_key, evaluate, keep


def selection(wns):
    return {"candidate_id": STRATEGY,
            "native_selection": {"setup_scope_evidence": {"wns_ns": wns}}}


class ReplayTests(unittest.TestCase):
    def test_boundary(self):
        self.assertTrue(keep(selection(-2.0), -2.0))
        self.assertFalse(keep(selection(-2.01), -2.0))

    def test_unknown_retained(self):
        for value in (None, True, "-2", float("nan"), float("inf")):
            self.assertTrue(keep(selection(value), -2.0))

    def test_drc_unchanged(self):
        self.assertTrue(keep({"candidate_id": "pin_side_rebalance"}, -1.0))

    def test_outcome_not_used_for_selection(self):
        s = selection(-2.7)
        self.assertFalse(keep(s, -2.0))
        s.update(task_id="arbitrary", clean=True, final_wns=1.0)
        self.assertFalse(keep(s, -2.0))

    def test_effect_order_independent(self):
        self.assertEqual(effect_key({"config_edits": {"a": "1", "b": "2"}}),
                         effect_key({"config_edits": {"b": "2", "a": "1"}}))

    def test_skips_remain_in_denominator(self):
        rows = [{"task_id": "success", "original_clean": True, "attempts": [
            {"selection": selection(-2.7), "clean": True, "seconds": 12}]},
            {"task_id": "unmatched", "original_clean": False, "attempts": []}]
        result = evaluate(rows, -2.0)
        self.assertEqual(len(result["tasks"]), 2)
        self.assertEqual(result["replayed_clean"], 0)
        self.assertEqual(result["lost_successes"], ["success"])
        self.assertEqual(result["skipped_historical_attempt_seconds"], 12)


if __name__ == "__main__":
    unittest.main()
