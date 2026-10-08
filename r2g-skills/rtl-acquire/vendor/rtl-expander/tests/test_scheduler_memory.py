import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from frontier import FrontierDB, default_frontier_path, utc_now  # noqa: E402
from scheduler import reprioritize, seed_queries  # noqa: E402

SPEC = importlib.util.spec_from_file_location("scheduler_memory", SCRIPTS / "scheduler_memory.py")
memory = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(memory)


class SchedulerMemoryTests(unittest.TestCase):
    def build_source(self, root: Path) -> Path:
        corpus = root / "source"
        with FrontierDB(default_frontier_path(corpus)) as db:
            db.upsert_repository(
                {"url": "https://github.com/private-example/cpu", "provider": "github"},
                "keyword", priority=1.0,
            )
            db.add_query("github", "keyword", "old secret cursor query", 1.0, 10)
            db.connection.execute(
                "UPDATE queries SET cursor='SECRET_CURSOR' WHERE query_text='old secret cursor query'"
            )
            db.connection.execute(
                """INSERT INTO source_yield(
                     source_key,provider,strategy,candidates,acquired,new_design_instances,
                     new_families,synthesis_valid_families,cpu_hours,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("github:keyword:family:cpu", "github", "keyword", 10, 10,
                 9, 8, 8, 2.0, utc_now()),
            )
            db.connection.execute(
                "INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?)",
                ("phase2_source_observation:github:keyword:family:cpu",
                 json.dumps({"candidates": 10, "new_design_instances": 9,
                             "new_gold_families": 4, "resource_classes": {"LARGE": 4}}),
                 utc_now()),
            )
            db.connection.commit()
        return corpus

    def test_export_excludes_candidate_and_cursor_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.build_source(root)
            output = root / "warm.json"
            payload = memory.export_memory(source, output)
            text = output.read_text()
            self.assertEqual(set(payload["tables"]), {"source_yield", "scheduler_state"})
            self.assertNotIn("SECRET_CURSOR", text)
            self.assertNotIn("private-example", text)
            self.assertNotIn("old secret cursor query", text)

    def test_imported_memory_changes_fresh_query_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.build_source(root)
            output = root / "warm.json"
            memory.export_memory(source, output)

            cold = root / "cold"
            with FrontierDB(default_frontier_path(cold)) as db:
                seed_queries(db, ["github"], 10, ["open source cpu core verilog"])
                reprioritize(db)
                cold_priority = db.connection.execute("SELECT priority FROM queries").fetchone()[0]

            warm = root / "warm"
            memory.import_memory(warm, output)
            with FrontierDB(default_frontier_path(warm)) as db:
                seed_queries(db, ["github"], 10, ["open source cpu core verilog"])
                reprioritize(db)
                warm_priority = db.connection.execute("SELECT priority FROM queries").fetchone()[0]
                self.assertFalse(db.connection.execute("SELECT 1 FROM repositories").fetchone())
            self.assertGreater(warm_priority, cold_priority)

    def test_tampered_memory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "warm.json"
            memory.export_memory(self.build_source(root), output)
            payload = json.loads(output.read_text())
            payload["tables"]["source_yield"][0]["new_families"] = 999
            output.write_text(json.dumps(payload))
            with self.assertRaisesRegex(memory.MemoryError, "digest mismatch"):
                memory.import_memory(root / "target", output)

    def test_import_requires_fresh_frontier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "warm.json"
            memory.export_memory(self.build_source(root), output)
            target = root / "target"
            with FrontierDB(default_frontier_path(target)) as db:
                db.add_query("github", "keyword", "already used", 1.0, 1)
            with self.assertRaisesRegex(memory.MemoryError, "fresh frontier"):
                memory.import_memory(target, output)


if __name__ == "__main__":
    unittest.main()
