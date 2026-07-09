#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class DesignStatusIOTests(unittest.TestCase):
    def test_design_status_paths_and_payload_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_root = Path(tmpdir)
            design = "demo_design"

            status_root = out_root / "_design_status"
            status_root.mkdir(parents=True, exist_ok=True)
            status_path = status_root / f"{design}.json"
            stage_log = status_root / f"{design}.jsonl"

            payload = {
                "design": design,
                "stage": "synthesize",
                "state": "running",
                "updated_at": "2026-04-15T00:00:00+08:00",
                "top": "demo_top",
            }
            status_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            with stage_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"design": design, "stage": "queued", "state": "start"}) + "\n")
                fh.write(json.dumps({"design": design, "stage": "synthesize", "state": "running"}) + "\n")

            loaded = json.loads(status_path.read_text(encoding="utf-8"))
            lines = [json.loads(line) for line in stage_log.read_text(encoding="utf-8").splitlines() if line.strip()]

            self.assertEqual(loaded["design"], design)
            self.assertEqual(loaded["stage"], "synthesize")
            self.assertEqual(loaded["state"], "running")
            self.assertEqual(lines[0]["stage"], "queued")
            self.assertEqual(lines[1]["stage"], "synthesize")


if __name__ == "__main__":
    unittest.main()
