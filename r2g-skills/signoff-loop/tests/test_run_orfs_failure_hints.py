"""A stage failure must still reach run_orfs.sh's archive step.

After the stage loop breaks, run_orfs.sh prints failure HINTs and only THEN
copies results/ logs/ objects/ into backend/RUN_* and writes run-meta.json.
The script runs under `set -euo pipefail`, so any probe in the HINT block that
can fail aborts the whole script before the archive. The place branch looked
for `3_4_place_resized.tmp.log` with `$(ls -t ... | head -1)`: on a failure
before 3_4 the file does not exist, `ls` returns 2, pipefail propagates it and
the assignment kills the script. Every early place failure (nangate45 C01347,
PPL-0024) therefore archived no logs, and the agent's in-flow PPL die
correction, which reads those logs, never ran.

This executes the real HINT block, cut from run_orfs.sh, under the script's own
shell options for every stage, with no ORFS workspace present.
"""
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
RUN_ORFS = REPO / "r2g-skills" / "signoff-loop" / "scripts" / "flow" / "run_orfs.sh"


def _hint_block() -> str:
    text = RUN_ORFS.read_text()
    start = text.index("# Detect routing failure and suggest recovery")
    end = text.index("# Collect results (ORFS uses FLOW_VARIANT as subdirectory)")
    return text[start:end]


@pytest.mark.parametrize("stage", ["synth", "floorplan", "place", "cts", "grt", "route", "finish"])
@pytest.mark.parametrize("status", [2, 124])
def test_failure_hints_never_abort_before_archive(tmp_path, stage, status):
    backend = tmp_path / "RUN_X"
    backend.mkdir()
    (backend / "stage_log.jsonl").write_text(json.dumps({"stage": stage, "status": status}) + "\n")
    (backend / "flow.log").write_text("[ERROR PPL-0024] Number of IO pins (10) exceeds ...\n")
    script = tmp_path / "hint.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"BACKEND_DIR={backend}\nMAKE_STATUS={status}\n"
        f"FLOW_DIR={tmp_path / 'no_orfs'}\nPLATFORM=nangate45\nDESIGN_NAME=d\nFLOW_VARIANT=v\n"
        f"PROJECT_DIR={tmp_path}\nORFS_TIMEOUT=60\n"
        + _hint_block() + "\necho REACHED_ARCHIVE\n")
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=60)
    assert "REACHED_ARCHIVE" in r.stdout, (r.returncode, r.stdout[-500:], r.stderr[-500:])
