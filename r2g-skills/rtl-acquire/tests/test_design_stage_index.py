#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class DesignStageIndexTests(unittest.TestCase):
    def test_design_stage_index_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_root = Path(tmpdir)
            status_root = out_root / "_design_status"
            status_root.mkdir(parents=True, exist_ok=True)

            rows = [
                {"design": "d1", "stage": "synthesize", "state": "running", "updated_at": "2026-04-15T10:00:00+08:00"},
                {"design": "d2", "stage": "graph_convert", "state": "completed", "updated_at": "2026-04-15T10:01:00+08:00"},
                {"design": "d3", "stage": "exception", "state": "failed", "updated_at": "2026-04-15T10:02:00+08:00"},
            ]

            for row in rows:
                (status_root / f"{row['design']}.json").write_text(
                    json.dumps(row, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            stage_counts: dict[str, int] = {}
            state_counts: dict[str, int] = {}
            for row in rows:
                stage_counts[row["stage"]] = stage_counts.get(row["stage"], 0) + 1
                state_counts[row["state"]] = state_counts.get(row["state"], 0) + 1

            payload = {
                "updated_at": "2026-04-15T10:03:00+08:00",
                "out_root": str(out_root),
                "design_count": len(rows),
                "stage_counts": stage_counts,
                "state_counts": state_counts,
                "designs": sorted(rows, key=lambda row: (row["stage"], row["design"])),
            }

            index_path = status_root / "design_stage_index.json"
            index_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["design_count"], 3)
            self.assertEqual(loaded["stage_counts"]["synthesize"], 1)
            self.assertEqual(loaded["state_counts"]["failed"], 1)
            self.assertEqual(len(loaded["designs"]), 3)


if __name__ == "__main__":
    unittest.main()
