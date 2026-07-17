#!/usr/bin/env python3
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path


class PublishEligibilityPolicyTests(unittest.TestCase):
    def test_only_keep_or_conditional_non_low_fidelity_designs_are_publishable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            design_scores = root / "design_quality_scores.csv"
            external_index = root / "index.csv"
            publish_eligible = root / "publish_eligible_designs.csv"

            with design_scores.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "design",
                        "design_action",
                        "design_quality_score",
                        "graph_complexity_score",
                        "dominant_cell_share",
                        "low_fidelity",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "design": "good_keep",
                            "design_action": "keep",
                            "design_quality_score": "0.8",
                            "graph_complexity_score": "0.3",
                            "dominant_cell_share": "0.4",
                            "low_fidelity": "False",
                        },
                        {
                            "design": "good_conditional",
                            "design_action": "conditional",
                            "design_quality_score": "0.3",
                            "graph_complexity_score": "0.05",
                            "dominant_cell_share": "0.4",
                            "low_fidelity": "False",
                        },
                        {
                            "design": "bad_reject",
                            "design_action": "reject",
                            "design_quality_score": "0.01",
                            "graph_complexity_score": "0.2",
                            "dominant_cell_share": "0.3",
                            "low_fidelity": "False",
                        },
                        {
                            "design": "bad_low_fidelity",
                            "design_action": "keep",
                            "design_quality_score": "0.9",
                            "graph_complexity_score": "0.4",
                            "dominant_cell_share": "0.2",
                            "low_fidelity": "True",
                        },
                    ],
                )

            with external_index.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=["design", "status"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"design": "good_keep", "status": "success"},
                        {"design": "good_conditional", "status": "success"},
                        {"design": "bad_reject", "status": "success"},
                        {"design": "bad_low_fidelity", "status": "success"},
                    ]
                )

            # License/revision contract (2026-07-16 issue 2): the gate is
            # fail-closed on license, so the GOOD designs carry an 'allow'
            # provenance stamp in design_meta.json (as expansion now writes).
            import json as _json
            for d in ("good_keep", "good_conditional", "bad_reject",
                      "bad_low_fidelity"):
                ddir = external_index.parent / d
                ddir.mkdir(parents=True, exist_ok=True)
                (ddir / "design_meta.json").write_text(_json.dumps(
                    {"design": d, "source_kind": "local_tree",
                     "license_status": "allow",
                     "license_evidence": "LICENSE:MIT LICENSE"}), encoding="utf-8")

            policy = {
                "allowed_design_actions": ["keep", "conditional"],
                "exclude_low_fidelity_designs": True,
                "max_dominant_gate_share": 0.98,
                "min_nontrivial_complexity_score": 0.02,
            }
            policy_path = root / "publish_policy.json"
            policy_path.write_text(__import__("json").dumps(policy), encoding="utf-8")

            from importlib.util import module_from_spec, spec_from_file_location

            script = Path(__file__).resolve().parents[1] / "scripts" / "publish" / "build_publish_candidates.py"
            spec = spec_from_file_location("build_publish_candidates", script)
            mod = module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(mod)

            import subprocess

            subprocess.run(
                [
                    "python3",
                    str(script),
                    "--external-index",
                    str(external_index),
                    "--design-scores",
                    str(design_scores),
                    "--publish-policy-json",
                    str(policy_path),
                    "--out-csv",
                    str(publish_eligible),
                    "--out-json",
                    str(root / "publish_eligible_designs.json"),
                    "--out-md",
                    str(root / "publish_eligible_designs.md"),
                ],
                check=True,
            )

            with publish_eligible.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            selected = {row["design"] for row in rows if row["publish_eligible"] == "True"}
            self.assertEqual(selected, {"good_keep", "good_conditional"})


if __name__ == "__main__":
    unittest.main()
