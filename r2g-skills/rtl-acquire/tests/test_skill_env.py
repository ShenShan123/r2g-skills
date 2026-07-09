#!/usr/bin/env python3
"""skill_env resolution tests — the thin delegate over the shared r2g _env.sh.

Covers the three-layer resolution order (shell env > shared env/env.local.sh >
default), the R2G_ACQUIRE_* corpus-root namespace, and the sibling sub-skill
paths of the scoped-reuse contract (run_orfs.sh / netlist_graph.py)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import skill_env  # noqa: E402
from skill_env import (  # noqa: E402
    _parse_env_local,
    default_acquire_root,
    default_downloads_root,
    default_merged_manifest,
    default_out_root,
    default_python_bin,
    default_workspace_root,
    netlist_graph_script,
    resolve_path_env,
    resolve_str_env,
    run_orfs_script,
    workspace_path,
)


class EnvLocalParseTests(unittest.TestCase):
    def test_parse_env_local_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "env.local.sh"
            env_file.write_text(
                "# comment\n"
                "export ORFS_ROOT=/tmp/orfs\n"
                "export R2G_GRAPH_PYTHON='/tmp/venv/bin/python'\n"
                'export R2G_ACQUIRE_ROOT="/tmp/acq"\n'
                "export DERIVED=$R2G_ACQUIRE_ROOT/sub\n",
                encoding="utf-8",
            )
            loaded = _parse_env_local(env_file)
            self.assertEqual(loaded["ORFS_ROOT"], "/tmp/orfs")
            self.assertEqual(loaded["R2G_GRAPH_PYTHON"], "/tmp/venv/bin/python")
            self.assertEqual(loaded["R2G_ACQUIRE_ROOT"], "/tmp/acq")
            self.assertEqual(loaded["DERIVED"], "/tmp/acq/sub")

    def test_parse_env_local_missing_file(self) -> None:
        self.assertEqual(_parse_env_local(Path("/nonexistent/env.local.sh")), {})


class ResolutionOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)

    def test_shell_env_wins(self) -> None:
        os.environ["R2G_ACQUIRE_TEST_KEY"] = "from-shell"
        self.assertEqual(resolve_str_env("R2G_ACQUIRE_TEST_KEY", "default"), "from-shell")

    def test_default_when_unset(self) -> None:
        os.environ.pop("R2G_ACQUIRE_TEST_KEY", None)
        self.assertEqual(resolve_str_env("R2G_ACQUIRE_TEST_KEY", "default"), "default")

    def test_resolve_path_env_expands_user(self) -> None:
        os.environ["R2G_ACQUIRE_TEST_PATH"] = "~/somewhere"
        resolved = resolve_path_env("R2G_ACQUIRE_TEST_PATH", "/tmp/x")
        self.assertFalse(str(resolved).startswith("~"))


class CorpusRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)

    def test_acquire_root_override_cascades(self) -> None:
        os.environ["R2G_ACQUIRE_ROOT"] = "/tmp/acq_root"
        for name in ("R2G_ACQUIRE_WORKSPACE", "R2G_ACQUIRE_OUT",
                     "R2G_ACQUIRE_DOWNLOADS", "R2G_ACQUIRE_MERGED_MANIFEST"):
            os.environ.pop(name, None)
        self.assertEqual(default_acquire_root(), Path("/tmp/acq_root"))
        self.assertEqual(default_workspace_root(), Path("/tmp/acq_root/workspace"))
        self.assertEqual(default_out_root(), Path("/tmp/acq_root/corpus"))
        self.assertEqual(default_downloads_root(), Path("/tmp/acq_root/_downloads"))
        self.assertEqual(default_merged_manifest(),
                         Path("/tmp/acq_root/netlist_graph_corpus_manifest.csv"))
        self.assertEqual(workspace_path("failures/x.json"),
                         Path("/tmp/acq_root/workspace/failures/x.json"))

    def test_specific_root_beats_acquire_root(self) -> None:
        os.environ["R2G_ACQUIRE_ROOT"] = "/tmp/acq_root"
        os.environ["R2G_ACQUIRE_OUT"] = "/elsewhere/corpus"
        self.assertEqual(default_out_root(), Path("/elsewhere/corpus"))

    def test_default_root_under_repo_design_cases(self) -> None:
        for name in ("R2G_ACQUIRE_ROOT",):
            os.environ.pop(name, None)
        root = default_acquire_root()
        self.assertTrue(str(root).endswith("design_cases/_rtl_acquire"))

    def test_python_bin_defaults_to_interpreter(self) -> None:
        os.environ.pop("R2G_ACQUIRE_PYTHON", None)
        self.assertEqual(default_python_bin(), sys.executable)


class SiblingSkillTests(unittest.TestCase):
    """The scoped-reuse contract: rtl-acquire BORROWS these — they must exist."""

    def test_run_orfs_script_exists(self) -> None:
        self.assertTrue(run_orfs_script().is_file(),
                        f"missing sibling script: {run_orfs_script()}")

    def test_netlist_graph_script_exists(self) -> None:
        self.assertTrue(netlist_graph_script().is_file(),
                        f"missing sibling script: {netlist_graph_script()}")

    def test_shared_env_sh_is_byte_identical_copy(self) -> None:
        import hashlib
        ours = skill_env.ENV_SH
        sibling = skill_env.signoff_loop_dir() / "scripts" / "flow" / "_env.sh"
        self.assertTrue(ours.is_file())
        self.assertEqual(
            hashlib.md5(ours.read_bytes()).hexdigest(),
            hashlib.md5(sibling.read_bytes()).hexdigest(),
            "_env.sh must stay byte-identical across sub-skills (CLAUDE.md rule)",
        )


if __name__ == "__main__":
    unittest.main()
