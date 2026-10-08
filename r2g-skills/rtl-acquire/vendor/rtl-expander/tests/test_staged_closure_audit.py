import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import processing_queue
import run_expansion_round as rtl
import staged_closure_audit


class StagedClosureAuditTests(unittest.TestCase):
    def design(self, design_id: str, source_hash: str, salt: str):
        return {
            "design_id": design_id,
            "family_id": "provisional",
            "revision_id": rtl.stable_id("rev", design_id),
            "identity": {"repository_name": design_id, "project_key": design_id},
            "provenance": {
                "repository_url": f"https://example.test/{design_id}",
                "commit_sha": salt * 40,
            },
            "build": {"top_module": f"{design_id}_top", "dependency_modules": []},
            "source": {"source_units": [
                {"path": f"rtl/{design_id}.v", "language": "verilog", "sha256": source_hash}
            ]},
            "dedup": {
                "source_hash": salt * 64,
                "normalized_hash": chr(ord(salt) + 1) * 64,
                "hierarchy_hash": chr(ord(salt) + 2) * 64,
                "generic_netlist_hash": chr(ord(salt) + 3) * 64,
            },
            "quality": {"training_tier": "TRAINING_SILVER", "quality_flags": []},
        }

    def test_staged_bridge_reports_conflict_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            args = SimpleNamespace(
                organization_aware_split=False, split_seed="seed",
                train_percent=80, val_percent=10, split_reconciliation_plan=None,
            )
            left_hash, right_hash = "1" * 64, "2" * 64
            historical = {
                "left": self.design("left", left_hash, "a"),
                "right": self.design("right", right_hash, "e"),
            }
            rtl.assign_families_and_splits(historical, corpus, args)
            assignments = rtl.load_jsonl(
                corpus / "manifests/split_assignments.jsonl", "split_group_id"
            )
            groups = sorted(assignments)
            assignments[groups[0]]["split"] = "train"
            assignments[groups[1]]["split"] = "val"
            rtl.write_jsonl(corpus / "manifests/split_assignments.jsonl", assignments.values())

            bridge = self.design("bridge", "3" * 64, "i")
            bridge["source"]["source_units"] = [
                {"path": "left.v", "language": "verilog", "sha256": left_hash},
                {"path": "right.v", "language": "verilog", "sha256": right_hash},
            ]
            artifact = corpus / "state/repo_runs/bridge.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(rtl.json.dumps({"designs": [bridge]}))
            with processing_queue.ProcessingQueue(corpus) as queue:
                queue.enqueue("round", "provider:org/repo@commit", "/immutable/source")
                queue.finish(
                    "round", "provider:org/repo@commit", terminal_state="SYNTH_VALID",
                    run_key="bridge", artifact_path=str(artifact),
                )
            before = (corpus / "manifests/split_assignments.jsonl").read_bytes()
            result = staged_closure_audit.audit(corpus, "round")
            self.assertEqual(result["potential_train_val_components"], 1)
            self.assertEqual(result["potential_test_boundary_conflicts"], 0)
            self.assertEqual(result["conflicts"][0]["old_split_groups"], groups)
            self.assertFalse(result["publication_performed"])
            self.assertEqual(
                (corpus / "manifests/split_assignments.jsonl").read_bytes(), before
            )


if __name__ == "__main__":
    unittest.main()
