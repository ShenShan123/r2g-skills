import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from processing_queue import ProcessingQueue, exact_terminal_set


class PipelinedProcessingTests(unittest.TestCase):
    def make_frontier(self, root: Path, rows: list[tuple[str, str]]) -> Path:
        path = root / "state/frontier.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute(
            """CREATE TABLE repository_revisions(
                 repository_revision_key TEXT PRIMARY KEY,
                 source_path TEXT NOT NULL,
                 acquired_at TEXT)"""
        )
        connection.executemany(
            "INSERT INTO repository_revisions VALUES(?,?,?)",
            [(key, source, "2026-08-11T00:00:00+00:00") for key, source in rows],
        )
        connection.commit()
        connection.close()
        return path

    def test_queue_rebuild_claim_retry_and_exact_set_drain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frontier = self.make_frontier(root, [
                ("github:a/old@0", "/immutable/old"),
                ("github:a/new@1", "/immutable/new1"),
                ("github:a/new@2", "/immutable/new2"),
            ])
            with ProcessingQueue(root) as queue:
                inserted = queue.reconcile_frontier("round", frontier, {"github:a/old@0"})
                self.assertEqual(len(inserted), 2)
                first = queue.claim("round", "worker-1")
                self.assertIsNotNone(first)
                queue.fail("round", first["repository_revision_key"], "transient")
                retry = queue.claim("round", "worker-2")
                self.assertEqual(retry["repository_revision_key"], first["repository_revision_key"])
                queue.finish(
                    "round", retry["repository_revision_key"], terminal_state="NO_RTL",
                    run_key="rk1", artifact_path="/artifact/1",
                )
                second = queue.claim("round", "worker-3")
                queue.finish(
                    "round", second["repository_revision_key"], terminal_state="SYNTH_VALID",
                    run_key="rk2", artifact_path="/artifact/2",
                )
                expected = {"github:a/new@1", "github:a/new@2"}
                self.assertTrue(exact_terminal_set(queue, "round", expected))
                self.assertFalse(exact_terminal_set(queue, "round", expected | {"github:a/extra@3"}))
                self.assertEqual(queue.lifecycle_counts("round"), {
                    "acquired": 2, "started": 2, "terminal": 2,
                })

    def test_abandoned_processing_claim_is_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frontier = self.make_frontier(root, [("github:a/new@1", "/immutable/new1")])
            with ProcessingQueue(root) as queue:
                queue.reconcile_frontier("round", frontier, set())
                queue.claim("round", "dead-worker")
                self.assertEqual(queue.requeue_abandoned("round"), 1)
                recovered = queue.claim("round", "live-worker")
                self.assertEqual(recovered["attempts"], 2)

    def test_stage_only_builds_cache_without_publishing_global_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = root / "repositories/github/example/no-rtl" / ("a" * 40)
            source = revision / "source"
            source.mkdir(parents=True)
            (source / "README.md").write_text("software only\n")
            (revision / "repository.json").write_text(json.dumps({
                "commit_sha": "a" * 40,
                "canonical_url": "https://github.com/example/no-rtl",
            }))
            intake = root / "intake"
            intake.mkdir()
            (intake / "revision").symlink_to(source, target_is_directory=True)
            receipt = root / "receipt.json"
            result = subprocess.run([
                sys.executable, str(SCRIPTS / "run_expansion_round.py"),
                "--source-root", str(intake), "--corpus-root", str(root),
                "--repo", "revision", "--max-repos", "1", "--stage-only",
                "--staging-receipt", str(receipt),
            ], text=True, capture_output=True, timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            staged = json.loads(receipt.read_text())
            self.assertFalse(staged["publication_performed"])
            self.assertEqual(staged["repositories"][0]["terminal_state"], "NO_RTL")
            self.assertFalse((root / "manifests/all_designs.jsonl").exists())
            self.assertFalse((root / "manifests/repositories.jsonl").exists())
            cache_files = list((root / "state/repo_runs").glob("*.json"))
            self.assertEqual(len(cache_files), 1)
            cache_before = cache_files[0].read_bytes()

            publish = subprocess.run([
                sys.executable, str(SCRIPTS / "run_expansion_round.py"),
                "--source-root", str(intake), "--corpus-root", str(root),
                "--repo", "revision", "--max-repos", "1",
            ], text=True, capture_output=True, timeout=60, check=False)
            self.assertEqual(publish.returncode, 0, publish.stderr)
            repositories = (root / "manifests/repositories.jsonl").read_text()
            self.assertIn('"cache_status": "MISS"', repositories)
            self.assertEqual(cache_files[0].read_bytes(), cache_before)


if __name__ == "__main__":
    unittest.main()
