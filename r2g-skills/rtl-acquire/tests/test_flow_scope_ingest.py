#!/usr/bin/env python3
"""Convergence tests: synth-only flows ingest honestly into knowledge.sqlite.

Proves the Phase-4 contract end-to-end against the REAL signoff-loop ingest
(no mocks): a synth-only PASS ingests as pass (never 'partial'), a synth-only
FAIL carries its orfs-fail-synth failure_event, the frontend-diagnosis
projection adds the synth-frontend-<class> event + the exclude fix_event, and
the fast honesty check (fail count == frontend-event count) passes."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from skill_env import knowledge_dir  # noqa: E402

INGEST = knowledge_dir() / "ingest_run.py"
PROJECT_DIAG = SKILL_ROOT / "scripts" / "knowledge" / "project_frontend_diagnosis.py"


def make_project(root: Path, design: str, *, synth_pass: bool) -> Path:
    project = root / design
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir(parents=True)
    run_dir = project / "backend" / "RUN_2026-07-09_00-00-00"
    run_dir.mkdir(parents=True)
    (project / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = top_{design}\n"
        "export PLATFORM = nangate45\n"
        "export R2G_FLOW_SCOPE = synth_only\n"
        "export ABC_AREA = 0\n",
        encoding="utf-8",
    )
    (project / "constraints" / "constraint.sdc").write_text(
        "create_clock -name clk -period 10\n", encoding="utf-8")
    status = 0 if synth_pass else 1
    (run_dir / "stage_log.jsonl").write_text(
        json.dumps({"stage": "synth", "status": status, "elapsed_s": 12}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "flow.log").write_text(
        "yosys start\n" + ("" if synth_pass else "ERROR: syntax error, unexpected TOK\n"),
        encoding="utf-8",
    )
    return project


def ingest(project: Path, db: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(INGEST), str(project), "--db", str(db)],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"ingest failed: {result.stderr[-800:]}")


class FlowScopeIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "knowledge.sqlite"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _runs_row(self, design_prefix: str) -> sqlite3.Row:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM runs WHERE design_name LIKE ?",
            (f"top_{design_prefix}%",)).fetchone()
        conn.close()
        self.assertIsNotNone(row, f"no runs row for {design_prefix}")
        return row

    def test_synth_only_pass_is_pass_not_partial(self) -> None:
        project = make_project(self.root, "okdesign", synth_pass=True)
        ingest(project, self.db)
        row = self._runs_row("okdesign")
        self.assertEqual(row["flow_scope"], "synth_only")
        self.assertEqual(row["orfs_status"], "pass",
                         "synth-only pass must ingest as pass within its scope")

    def test_synth_only_fail_carries_failure_event(self) -> None:
        project = make_project(self.root, "baddesign", synth_pass=False)
        ingest(project, self.db)
        row = self._runs_row("baddesign")
        self.assertEqual(row["orfs_status"], "fail")
        self.assertEqual(row["orfs_fail_stage"], "synth")
        conn = sqlite3.connect(self.db)
        sigs = [r[0] for r in conn.execute(
            "SELECT signature FROM failure_events WHERE run_id=?",
            (row["run_id"],))]
        conn.close()
        self.assertTrue(any(s.startswith("orfs-fail-synth") for s in sigs), sigs)

    def test_full_scope_synth_pass_stays_partial(self) -> None:
        project = make_project(self.root, "fullflow", synth_pass=True)
        cfg = project / "constraints" / "config.mk"
        cfg.write_text(cfg.read_text().replace(
            "export R2G_FLOW_SCOPE = synth_only\n", ""), encoding="utf-8")
        ingest(project, self.db)
        row = self._runs_row("fullflow")
        self.assertEqual(row["flow_scope"], "full")
        self.assertEqual(row["orfs_status"], "partial",
                         "a full-scope run that only ran synth is still partial")

    def test_frontend_diagnosis_projection_and_honesty(self) -> None:
        projects_root = self.root / "synth_projects"
        projects_root.mkdir()
        project = make_project(projects_root, "frontfail", synth_pass=False)
        ingest(project, self.db)

        out_root = self.root / "corpus"
        out_root.mkdir()
        (out_root / "index.csv").write_text(
            "design,top,synth_variant,status,cells,comb_cells,seq_cells,nets,"
            "source_path,graph_format,duplicate_reason,notes\n"
            "frontfail,top_frontfail,yosys_abc_area0,synth_failed,,,,,"
            "/tmp/src.v,netlist_graph_v1,,invalid name for macro definition %%X\n",
            encoding="utf-8",
        )
        env = dict(__import__("os").environ)
        env["R2G_KNOWLEDGE_DB"] = str(self.db)
        result = subprocess.run(
            [sys.executable, str(PROJECT_DIAG),
             "--index-csv", str(out_root / "index.csv"),
             "--projects-root", str(projects_root)],
            capture_output=True, text=True, check=False, env=env)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])

        row = self._runs_row("frontfail")
        conn = sqlite3.connect(self.db)
        sigs = [r[0] for r in conn.execute(
            "SELECT signature FROM failure_events WHERE run_id=?",
            (row["run_id"],))]
        fix_count = conn.execute(
            "SELECT COUNT(*) FROM fix_events WHERE fix_session_id=?",
            ("acquire-frontend-frontfail",)).fetchone()[0]
        conn.close()
        self.assertIn("synth-frontend-template_placeholder", sigs, sigs)
        self.assertTrue(any(s.startswith("orfs-fail-synth") for s in sigs), sigs)
        self.assertEqual(fix_count, 1, "exclude decision must land as a fix_event")

        # The fast honesty check must pass once the projection ran.
        check = subprocess.run(
            [sys.executable, str(PROJECT_DIAG), "--check", str(self.db)],
            capture_output=True, text=True, check=False)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()
