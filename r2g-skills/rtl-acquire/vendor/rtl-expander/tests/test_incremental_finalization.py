import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finalize_staged_round
import materialize_storage_layout
import run_factory_round
import run_until_revision_target
import run_until_family_target
import summarize_scale_pilot
from corpus_state import CorpusState
from processing_queue import ProcessingQueue


class IncrementalFinalizationTests(unittest.TestCase):
    def test_exact_terminal_staging_artifacts_are_consumed_without_processing(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            key = "github:example/core@" + "a" * 40
            artifact = corpus / "state/repo_runs/run.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(json.dumps({
                "run_key": "run",
                "repository": {
                    "repo_id": "r1", "repository_revision_key": key,
                    "state": "SYNTH_VALID", "stage_status": {},
                },
                "designs": [{
                    "design_id": "d1", "source": {"repository_revision_key": key},
                }],
            }) + "\n")
            with ProcessingQueue(corpus) as queue:
                queue.enqueue("round", key, "/immutable/source")
                row = queue.claim("round", "worker")
                self.assertIsNotNone(row)
                queue.finish(
                    "round", key, terminal_state="SYNTH_VALID",
                    run_key="run", artifact_path=str(artifact),
                )
            repos, designs = finalize_staged_round.staged_payloads(corpus, "round", {key})
            self.assertEqual(repos[0]["stage_status"]["PUBLISHED"], "DONE")
            self.assertEqual([row["design_id"] for row in designs], ["d1"])

    def test_terminal_set_must_equal_cohort_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            with ProcessingQueue(corpus) as queue:
                queue.enqueue("round", "github:x/y@a", "/tmp/x")
            with self.assertRaisesRegex(RuntimeError, "exactly match cohort"):
                finalize_staged_round.staged_payloads(
                    corpus, "round", {"github:x/y@a"},
                )

    def test_missing_artifact_revision_key_is_backfilled_from_locked_context(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            key = "codeberg:example/software@" + "b" * 40
            artifact = corpus / "state/repo_runs/no-rtl.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(json.dumps({
                "run_key": "no-rtl",
                "repository": {
                    "repo_id": "r-no-rtl", "state": "NO_RTL",
                    "stage_status": {},
                },
                "designs": [],
            }) + "\n")
            with ProcessingQueue(corpus) as queue:
                queue.enqueue("round", key, "/immutable/source")
                self.assertIsNotNone(queue.claim("round", "worker"))
                queue.finish(
                    "round", key, terminal_state="NO_RTL",
                    run_key="no-rtl", artifact_path=str(artifact),
                )
            repos, designs = finalize_staged_round.staged_payloads(
                corpus, "round", {key},
            )
            self.assertEqual(repos[0]["repository_revision_key"], key)
            self.assertEqual(designs, [])

    def test_conflicting_artifact_revision_key_hard_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            key = "github:example/core@" + "c" * 40
            other = "github:example/core@" + "d" * 40
            artifact = corpus / "state/repo_runs/conflict.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(json.dumps({
                "run_key": "conflict",
                "repository": {
                    "repo_id": "r-conflict", "repository_revision_key": other,
                    "state": "NO_RTL", "stage_status": {},
                },
                "designs": [],
            }) + "\n")
            with ProcessingQueue(corpus) as queue:
                queue.enqueue("round", key, "/immutable/source")
                self.assertIsNotNone(queue.claim("round", "worker"))
                queue.finish(
                    "round", key, terminal_state="NO_RTL",
                    run_key="conflict", artifact_path=str(artifact),
                )
            with self.assertRaisesRegex(RuntimeError, "revision identity mismatch"):
                finalize_staged_round.staged_payloads(corpus, "round", {key})

    def test_locked_queue_and_run_key_identity_must_all_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            key = "github:example/core@" + "e" * 40
            artifact = corpus / "state/repo_runs/artifact-run.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(json.dumps({
                "run_key": "different-run",
                "repository": {
                    "repo_id": "r1", "repository_revision_key": key,
                    "state": "NO_DESIGN", "stage_status": {},
                },
                "designs": [],
            }) + "\n")
            with ProcessingQueue(corpus) as queue:
                queue.enqueue("round", key, "/immutable/source")
                self.assertIsNotNone(queue.claim("round", "worker"))
                queue.finish(
                    "round", key, terminal_state="NO_DESIGN",
                    run_key="artifact-run", artifact_path=str(artifact),
                )
            with self.assertRaisesRegex(RuntimeError, "run-key mismatch"):
                finalize_staged_round.staged_payloads(corpus, "round", {key})

    def test_admission_hash_mode_does_not_rehash_immutable_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            unit = source / "top.v"
            original = b"module top; endmodule\n"
            unit.write_bytes(original)
            record = {"source": {"source_units": [{
                "path": "top.v", "sha256": hashlib.sha256(original).hexdigest(),
            }]}}
            self.assertTrue(materialize_storage_layout.verified_source_units(record, source))
            unit.write_bytes(b"changed")
            self.assertFalse(materialize_storage_layout.verified_source_units(record, source))
            self.assertTrue(materialize_storage_layout.admitted_source_units(record, source))

    def test_normal_scale_summary_trusts_admission_digest_without_byte_rehash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            unit = source / "top.v"
            admitted = b"module top; endmodule\n"
            unit.write_bytes(b"mutated after admission")
            designs = [{
                "source": {
                    "repository_revision_key": "github:x/y@abc",
                    "source_units": [{
                        "path": "top.v", "sha256": hashlib.sha256(admitted).hexdigest(),
                    }],
                },
                "storage": {"repository_source_path": str(source)},
            }]
            metadata = summarize_scale_pilot.verify_source_hashes(designs)
            deep = summarize_scale_pilot.verify_source_hashes(
                designs, mode="full-rehash",
            )
            self.assertEqual(metadata["source_units_checked"], 0)
            self.assertEqual(metadata["immutable_source_hash_mismatches"], 0)
            self.assertEqual(deep["source_units_checked"], 1)
            self.assertEqual(deep["immutable_source_hash_mismatches"], 1)

    def test_change_set_logical_hash_is_order_independent(self):
        left = {
            "d2": {"family_id": "f2", "split_group_id": "sg2", "split": "val"},
            "d1": {"family_id": "f1", "split_group_id": "sg1", "split": "train"},
        }
        right = dict(reversed(list(left.items())))
        self.assertEqual(
            finalize_staged_round.logical_membership_hash(left),
            finalize_staged_round.logical_membership_hash(right),
        )

    def test_batch_eight_activates_incremental_finalization(self):
        self.assertFalse(run_until_revision_target.incremental_finalization_enabled(
            "p2f_20260812_design-family-10k_batch0007"
        ))
        self.assertTrue(run_until_revision_target.incremental_finalization_enabled(
            "p2f_20260813_design-family-10k_batch0008"
        ))

    def test_family_controller_auto_resumes_only_unambiguous_child_states(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            controller = corpus / "quality/phase2/rounds/round/target_controller.json"
            controller.parent.mkdir(parents=True)
            controller.write_text(json.dumps({"state": "ACQUIRING_BACKOFF"}))
            self.assertTrue(
                run_until_family_target.child_failure_is_automatically_recoverable(
                    corpus, "round"
                )
            )
            controller.write_text(json.dumps({"state": "FAILED_FINALIZATION"}))
            self.assertFalse(
                run_until_family_target.child_failure_is_automatically_recoverable(
                    corpus, "round"
                )
            )
            controller.write_text(json.dumps({"state": "HARD_FAIL_COHORT_LOCK_HASH_CHANGED"}))
            self.assertFalse(
                run_until_family_target.child_failure_is_automatically_recoverable(
                    corpus, "round"
                )
            )

    def test_finalization_attempts_are_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            first, first_path = run_factory_round.start_attempt(
                corpus, "round", "incremental_finalization_v1"
            )
            second, second_path = run_factory_round.start_attempt(
                corpus, "round", "incremental_finalization_v1"
            )
            self.assertEqual((first, second), ("attempt_0001", "attempt_0002"))
            self.assertTrue(first_path.is_file())
            self.assertTrue(second_path.is_file())

    def test_preflight_plan_hash_ignores_timestamp_but_binds_content(self):
        plan = {
            "schema": finalize_staged_round.FINALIZATION_PLAN_SCHEMA,
            "state": "FINALIZATION_PLAN_READY",
            "round_id": "round",
            "created_at": "first",
            "round_change_set_preview": {"changed_design_ids": ["d1"]},
        }
        digest = finalize_staged_round.plan_digest(plan)
        plan["created_at"] = "second"
        self.assertEqual(finalize_staged_round.plan_digest(plan), digest)
        plan["round_change_set_preview"]["changed_design_ids"].append("d2")
        self.assertNotEqual(finalize_staged_round.plan_digest(plan), digest)

    def test_transient_finalization_failure_retries_in_same_attempt(self):
        locked = {
            "stage": "finalization_preflight", "state": "FAIL",
            "stderr_tail": "sqlite3.OperationalError: database is locked",
        }
        passed = {"stage": "retry", "state": "PASS", "returncode": 0}
        with mock.patch.object(
            run_factory_round, "run_stage", side_effect=[locked, passed],
        ) as run, mock.patch.object(run_factory_round.time, "sleep") as sleep:
            result = run_factory_round.run_finalization_stage(
                "finalization_preflight", ["command"], False,
                base_backoff_seconds=0,
            )
        self.assertEqual(result["state"], "PASS")
        self.assertEqual(len(result["transient_subattempts"]), 2)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once()

    def test_correctness_failure_is_never_retried(self):
        mismatch = {
            "stage": "finalization_preflight", "state": "FAIL",
            "stderr_tail": "RuntimeError: cohort terminal exact mismatch",
        }
        with mock.patch.object(
            run_factory_round, "run_stage", return_value=mismatch,
        ) as run, mock.patch.object(run_factory_round.time, "sleep") as sleep:
            result = run_factory_round.run_finalization_stage(
                "finalization_preflight", ["command"], False,
            )
        self.assertEqual(result["state"], "FAIL")
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()

    def test_preflight_builds_ready_plan_without_mutating_corpus_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            key = "github:example/no-rtl@" + "f" * 40
            existing = {
                "design_id": "d-existing", "family_id": "f-existing",
                "split_group_id": "sg-existing", "split": "train",
                "source": {"repository_revision_key": "github:x/y@abc"},
                "provenance": {
                    "repo_id": "r-existing", "repository_url": "https://github.com/x/y",
                    "commit_sha": "abc",
                },
                "identity": {"project_key": "p", "repository_name": "y"},
                "build": {"top_module": "top", "dependency_modules": []},
                "dedup": {}, "synthesis": {"generic_pass": True},
                "quality": {"training_tier": "TRAINING_SILVER"},
                "release": {"license_status": "UNKNOWN", "release_policy": "QUARANTINE"},
                "verification": {"functional_confidence": "F0"},
                "contamination": {"benchmark_contaminated": False},
            }
            with CorpusState(corpus) as state:
                state.apply_incremental(designs=[existing])
            artifact = corpus / "state/repo_runs/no-rtl.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(json.dumps({
                "run_key": "no-rtl",
                "repository": {"repo_id": "r-no-rtl", "state": "NO_RTL", "stage_status": {}},
                "designs": [],
            }))
            with ProcessingQueue(corpus) as queue:
                queue.enqueue("round", key, "/immutable/source")
                self.assertIsNotNone(queue.claim("round", "worker"))
                queue.finish(
                    "round", key, terminal_state="NO_RTL",
                    run_key="no-rtl", artifact_path=str(artifact),
                )
            cohort = corpus / "cohort_lock.json"
            key_digest = hashlib.sha256(
                json.dumps([key], separators=(",", ":")).encode()
            ).hexdigest()
            cohort.write_text(json.dumps({
                "revision_keys": [key], "acquired_revision_count": 1,
                "cohort_size": 1, "revision_keys_sha256": key_digest,
            }))
            round_dir = corpus / "quality/phase2/rounds/round"
            round_dir.mkdir(parents=True)
            cohort_hash = hashlib.sha256(cohort.read_bytes()).hexdigest()
            (round_dir / "cohort_lock.admission.json").write_text(json.dumps({
                "schema": "rtl_immutable_artifact_admission_v1",
                "object_id": "cohort_lock.json", "sha256": cohort_hash,
                "size": cohort.stat().st_size, "rehash_required": False,
            }))
            args = type("Args", (), {
                "corpus_root": corpus, "round_id": "round", "cohort_lock": cohort,
                "split_reconciliation_plan": None, "split_seed": "seed",
                "train_percent": 90, "val_percent": 5,
                "organization_aware_split": False,
            })()
            with CorpusState(corpus) as state:
                before = state.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            prepared = finalize_staged_round.prepare_finalization(args)
            with CorpusState(corpus) as state:
                after = state.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(before, after)
            self.assertEqual(prepared["plan"]["state"], "FINALIZATION_PLAN_READY")
            self.assertEqual(
                prepared["plan"]["terminal_identity"]["backfilled_revision_key_count"], 1,
            )


if __name__ == "__main__":
    unittest.main()
