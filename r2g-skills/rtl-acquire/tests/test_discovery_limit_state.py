from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "acquire" / "discover_download_candidates.py"


class DiscoveryLimitStateTests(unittest.TestCase):
    def test_limit_does_not_mark_unvisited_repos_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            downloads = root / "downloads"
            for repo_name, module_name in (
                ("repo_a", "alpha_controller"),
                ("repo_b", "beta_controller"),
            ):
                repo = downloads / repo_name
                repo.mkdir(parents=True)
                body = "\n".join(f"  wire signal_{index};" for index in range(50))
                (repo / f"{module_name}.v").write_text(
                    f"module {module_name}(input wire clk, output wire done);\n"
                    f"{body}\nassign done = clk;\nendmodule\n",
                    encoding="utf-8",
                )

            out_csv = root / "candidates.csv"
            scan_state = root / "scan_state.json"
            env = os.environ.copy()
            for name in ("PYTHONHOME", "PYTHONEXECUTABLE", "PYTHONPATH"):
                env.pop(name, None)
            env.update(
                {
                    "R2G_ACQUIRE_ROOT": str(root / "acq"),
                    "R2G_ACQUIRE_WORKSPACE": str(root / "workspace"),
                    "R2G_ACQUIRE_OUT": str(root / "external"),
                    "R2G_ACQUIRE_SEED_ROOT": str(root / "orfs"),
                }
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--downloads-root",
                    str(downloads),
                    "--out-csv",
                    str(out_csv),
                    "--scan-state-json",
                    str(scan_state),
                    "--no-sync-upstream",
                    "--limit",
                    "1",
                ],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            repos = json.loads(scan_state.read_text(encoding="utf-8"))["repos"]
            self.assertEqual(
                set(repos),
                {"repo_a"},
                msg=f"candidates={out_csv.read_text(encoding='utf-8')} state={repos}",
            )
            self.assertEqual(repos["repo_a"]["status"], "scanned")


if __name__ == "__main__":
    unittest.main()
