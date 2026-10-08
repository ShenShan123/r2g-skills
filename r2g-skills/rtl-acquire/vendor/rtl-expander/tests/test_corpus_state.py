import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import corpus_state
import process_runner


class CorpusStateTests(unittest.TestCase):
    def design(self, design_id: str, family_id: str, complete: bool = True) -> dict:
        return {
            "design_id": design_id, "family_id": family_id,
            "source": {"repository_revision_key": "github:x/y@abc" if complete else None},
            "provenance": {"repository_url": "https://github.com/x/y" if complete else "UNKNOWN",
                           "commit_sha": "abc" if complete else "UNKNOWN"},
            "synthesis": {"generic_pass": True}, "split": "train", "split_group_id": "sg1",
            "release": {"license_status": "UNKNOWN", "release_policy": "QUARANTINE"},
            "quality": {"training_tier": "TRAINING_SILVER"},
            "verification": {"functional_confidence": "F0"},
        }

    def test_ledger_is_idempotent_and_formal_family_metric_excludes_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifests").mkdir()
            rows = [self.design("d1", "f1"), self.design("d2", "f2", False)]
            (root / "manifests/all_designs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            (root / "manifests/repositories.jsonl").write_text("")
            with corpus_state.CorpusState(root) as state:
                first = state.sync_materialized_views()
                second = state.sync_materialized_views()
                events = state.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(first["formal_synthesis_valid_families"], 1)
            self.assertEqual(first["unresolved_provenance_unquarantined"], 0)
            self.assertEqual(second["design_changes"], 0)
            self.assertEqual(events, 10)

    def test_immutable_snapshot_has_content_bound_release_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifests").mkdir()
            row = self.design("d1", "f1")
            (root / "manifests/all_designs.jsonl").write_text(json.dumps(row) + "\n")
            (root / "manifests/repositories.jsonl").write_text("")
            (root / "quality").mkdir()
            (root / "quality/scale_pilot_summary.json").write_text(json.dumps({
                "stage_conservation": {"x": {"y": {"conserved": True, "residual": 0}}},
                "integrity": {
                    "corrupt_manifest_rows": 0, "duplicate_design_ids": 0,
                    "frontier": {"duplicate_revision_rows": 0},
                    "immutable_source_hash_mismatches": 0, "family_split_violations": 0,
                    "immutable_source_rehash_required": 0,
                    "split_group_violations": 0, "published_without_elaboration": 0,
                    "storage_layout_missing_design_json": 0,
                    "publish_invariants": {"valid": True},
                },
            }))
            (root / "benchmark_registry").mkdir()
            registry_hash = corpus_state.digest_tree(root / "benchmark_registry")
            (root / "quality/phase1_5").mkdir()
            (root / "quality/phase1_5/benchmark_contamination_audit.json").write_text(json.dumps({
                "benchmark_registry_hash": registry_hash, "matched_designs": 0,
            }))
            gold = root / "manifests/training_gold.jsonl"
            gold.write_text("")
            gold_hash = hashlib.sha256(gold.read_bytes()).hexdigest()
            (root / "manifests/training_gold.jsonl.admission.json").write_text(json.dumps({
                "schema": "rtl_materialized_view_admission_v1",
                "object_id": "training_gold.jsonl", "sha256": gold_hash,
                "size": 0, "rehash_required": False,
            }))
            (root / "manifests/training_gold.meta.json").write_text(json.dumps({
                "manifest_sha256": gold_hash,
            }))
            identity = corpus_state.materialize_snapshot(root, "snapshot-test")
            self.assertEqual(identity["schema"], corpus_state.RELEASE_SCHEMA)
            completion = json.loads((root / "snapshots/snapshot-test/completion.json").read_text())
            self.assertEqual(completion["status"], "CERTIFIED")
            self.assertTrue((root / "snapshots/snapshot-test/manifests/provenance_complete_synthesis_valid.jsonl").is_file())

    def test_repository_alias_preserves_unique_revision_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with corpus_state.CorpusState(root) as state:
                rows = [
                    {"repo_id": "r1", "repository_revision_key": "github:x/y@abc"},
                    {"repo_id": "r2", "repository_revision_key": "github:x/y@abc"},
                ]
                with state.connection:
                    state.upsert_repositories(rows)
                self.assertEqual(state.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()[0], 1)
                self.assertEqual(state.connection.execute("SELECT COUNT(*) FROM repository_aliases").fetchone()[0], 1)

    def test_uncertified_snapshot_does_not_advance_latest_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifests").mkdir()
            (root / "manifests/all_designs.jsonl").write_text("")
            (root / "manifests/repositories.jsonl").write_text("")
            corpus_state.materialize_snapshot(root, "uncertified")
            completion = json.loads((root / "snapshots/uncertified/completion.json").read_text())
            self.assertEqual(completion["status"], "NEEDS_HARDENING")
            self.assertFalse((root / "snapshots/latest_release.json").exists())

    def test_streamed_runner_persists_logs_and_bounded_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            result = process_runner.run_streamed(
                [sys.executable, "-c", "print('x' * 10000)"], Path(directory), "stage", tail_bytes=128,
            )
            self.assertEqual(result["returncode"], 0)
            self.assertLessEqual(len(result["stdout_tail"].encode()), 128)
            self.assertGreater(Path(result["stdout_log"]).stat().st_size, 10000)

    def test_processing_event_microbatch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [
                ("REVISION_ACQUIRED", "github:x/a@1", {"round_id": "r"}),
                ("REVISION_ACQUIRED", "github:x/b@2", {"round_id": "r"}),
            ]
            with corpus_state.CorpusState(root) as state:
                self.assertEqual(state.record_processing_events(events), 2)
                self.assertEqual(state.record_processing_events(events), 0)
                count = state.connection.execute(
                    "SELECT COUNT(*) FROM events WHERE stream='processing'"
                ).fetchone()[0]
            self.assertEqual(count, 2)

    def test_contaminated_license_clean_design_is_not_public_snapshot_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with corpus_state.CorpusState(root) as state:
                row = self.design("d1", "f1")
                row["release"]["release_policy"] = "PUBLIC_EXPORT_ALLOWED"
                row["quality"]["training_tier"] = "TRAINING_EXCLUDED"
                row["contamination"] = {"benchmark_contaminated": True}
                state.apply_incremental(designs=[row])
                exported = list(state.iter_payloads(
                    "release_policy='PUBLIC_EXPORT_ALLOWED' AND "
                    "COALESCE(json_extract(payload_json,'$.contamination.benchmark_contaminated'),0)=0"
                ))
            self.assertEqual(exported, [])
            self.assertEqual(row["release"]["release_policy"], "PUBLIC_EXPORT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
