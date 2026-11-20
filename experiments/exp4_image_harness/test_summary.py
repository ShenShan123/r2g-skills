"""Check image scores use the declared metric and retain unknown/incomplete costs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "exp4_harness"))
from summarize import summarize_run


class SummaryTests(unittest.TestCase):
    def test_image_metric_and_missing_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "runs/gpt_native/s1"
            output = run / "production/design"
            (output / "score").mkdir(parents=True)
            (run / "result.json").write_text(json.dumps({"wall_seconds": 10, "cpu_seconds": 5,
                "peak_process_tree_rss_bytes": 100, "usage_complete": False,
                "provider_usage": {"total_tokens": 99}}))
            (output / "run.json").write_text(json.dumps({"wall_seconds": 2, "cpu_seconds": 1,
                "peak_process_tree_rss_bytes": 20}))
            (output / "score/score.json").write_text(json.dumps({"design": "design", "platform": "gf180",
                "strict_full_pass": False, "label_accuracy": 1.0, "raster_accuracy": 0.75}))
            expected = [{"design": "design", "platform": "gf180"}]
            image = summarize_run(root, "gpt_native", 1, expected, "raster_accuracy")
            graph = summarize_run(root, "gpt_native", 1, expected)
            self.assertEqual(image["status"], "complete")
            self.assertEqual(image["raster_accuracy"], 0.75)
            self.assertEqual(graph["label_accuracy"], 1.0)
            self.assertEqual(image["total_wall_seconds"], 12)
            self.assertIsNone(image["total_tokens"])
            (output / "score/score.json").unlink()
            incomplete = summarize_run(root, "gpt_native", 1, expected, "raster_accuracy")
            self.assertEqual(incomplete["status"], "incomplete")
            self.assertIsNone(incomplete["raster_accuracy"])


if __name__ == "__main__":
    unittest.main()
