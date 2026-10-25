"""Step 6: sandbox execute adapter (design doc 10, 21.3).

TEHM does not own execution — the R2G shared base does (engineer_loop,
run_orfs.sh, fix_signoff.sh, _bounded_run). This adapter wraps any executor
callable with a fixed contract:

    executor(action: dict, context: RepairContext) -> ExecutionEvidence

``ExecutionEvidence`` = ``{"before_state": dict, "after_state": dict,
"observation_delta": dict, "tool_versions": dict}`` where the states use the
canonical state-content shape (config / reports / failure_signature).
Production wires run_orfs/fix_signoff here; tests inject fakes.
"""
from __future__ import annotations

from typing import Callable

from contracts import RepairContext

EXECUTE_VERSION = "execute-adapter-v0.1"


def execute_action(action: dict, context: RepairContext, *,
                   executor: Callable | None = None) -> dict | None:
    """Run one instantiated action through the execution base.

    Returns ``ExecutionEvidence`` (or None when no executor is wired — the
    pipeline then reports executability without executing).
    """
    if executor is None:
        return None
    return executor(action, context)
