"""A synthesis tool that died on a signal must not become a verdict on the RTL.

CORRECTIONS #15/#25 (wave 3): OpenROAD/OpenSTA crashes (Signal 11, SIGABRT
assertions) are load-dependent and surface as an ordinary non-zero stage exit.
In rtl-acquire, a Yosys/ABC crash leaves a terminal `make: *** ... Error 139`
line, so the evidence-absent guard does not fire and classify_failed_candidates
fell through to `exclude / low_value_failure`, a permanent exclusion of the
source. A crash is a TOOL failure: defer it (candidate kept, no negative
source-quality evidence).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS / "repair"))

from classify_failed_candidates import classify  # noqa: E402

CRASHES = (
    "Command terminated by signal 11 | Elapsed time: 0:41.02[h:]min:sec. | "
    "make: *** [Makefile:291: results/nangate45/top/base/1_1_yosys.v] Error 139",
    "yosys-abc: src/aig/gia/giaMan.c:123: Gia_ManStop: Assertion `p' failed. | "
    "Command terminated by signal 6",
    "Segmentation fault (core dumped) | make: *** [synth] Error 139",
    "Signal 11 received | Stack trace: sta::ClkInfo::refsFilter",
)


def test_crash_by_signal_is_deferred_not_excluded() -> None:
    for notes in CRASHES:
        assert classify("/c/rtl/top.v", notes, status="synth_failed") == (
            "defer", "tool_crash"), notes


def test_plain_nonzero_exit_is_still_a_source_failure() -> None:
    notes = ("ERROR: syntax error, unexpected ';' | Command exited with non-zero "
             "status 1 | make: *** [synth] Error 1")
    assert classify("/c/rtl/top.v", notes, status="synth_failed") == (
        "exclude", "low_value_failure")


def test_memory_guard_keeps_precedence_over_crash() -> None:
    notes = ("ERROR: Synthesized memory size 25856 exceeds SYNTH_MEMORY_MAX_BITS 4096 | "
             "make: *** [synth] Error 139")
    assert classify("/c/rtl/top.v", notes) == ("retry", "memory_limit")
