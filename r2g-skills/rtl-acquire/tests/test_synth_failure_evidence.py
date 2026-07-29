"""Authoritative synthesis-failure evidence + terminal classification.

Held-out V3 findings P1-HO-01 / P1-HO-03 (docs/superpowers/plans/
2026-07-28-heldout-v3-{generalization-analysis,remediation-plan}.md):

  * `synth_log_from` copied the FIRST EXISTING log name, which is the SUCCESSFUL
    `1_1_yosys_canonicalize.log` whenever the later mapping step failed. The
    memory-guard exception never reached synth.log, so mor1kx / JPEG / audio were
    classified `low_value_failure` and PERMANENTLY excluded while being provably
    synthesizable at a raised cap.
  * every unrecognised failure fell through to `exclude, low_value_failure`, so a
    missing `slang.so` (a TOOL gap) was recorded as bad RTL.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS / "repair"))

from execute.expand_candidates import (  # noqa: E402
    memory_limit_evidence,
    select_synth_failure_log,
    summarize_synth_failure,
    synth_log_from,
)
from classify_failed_candidates import classify  # noqa: E402

MEM_ABORT = (
    "Executing MEMORY_COLLECT pass.\n"
    "ERROR: Synthesized memory size 25856 exceeds SYNTH_MEMORY_MAX_BITS 4096.\n"
)
CANONICALIZE_OK = (
    "Executing HIERARCHY pass.\n"
    "Executing PROC pass.\n"
    "End of script. Logfile hash: abc123\n"
    "Yosys 0.44 (git sha1 deadbeef)\n"
)


def _run_dir(root: Path, logs: dict[str, str], flow_log: str | None = None) -> Path:
    run = root / "RUN_1"
    (run / "logs").mkdir(parents=True)
    for i, (name, text) in enumerate(sorted(logs.items())):
        p = run / "logs" / name
        p.write_text(text, encoding="utf-8")
        # Deterministic, distinct mtimes so ranking is not decided by ties.
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    if flow_log is not None:
        (run / "flow.log").write_text(flow_log, encoding="utf-8")
    return run


class SelectFailureLogTests(unittest.TestCase):
    def test_failing_mapping_log_beats_successful_canonicalize_log(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = _run_dir(root, {
                "1_1_yosys_canonicalize.log": CANONICALIZE_OK,
                "1_2_yosys.log": MEM_ABORT,
            })
            chosen, ev = select_synth_failure_log(run)
            self.assertEqual(chosen.name, "1_2_yosys.log")
            self.assertTrue(ev["has_terminal_error"])
            self.assertEqual(ev["diagnostic"], "ok")
            self.assertTrue(ev["log_sha256"])

    def test_legacy_first_existing_name_no_longer_wins(self) -> None:
        # The exact pre-fix ordering trap: 1_1_yosys.log exists and is CLEAN,
        # the error is only in flow.log. Old code copied 1_1_yosys.log.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = _run_dir(root, {"1_1_yosys.log": CANONICALIZE_OK},
                           flow_log="make: *** [synth] Error 1\n" + MEM_ABORT)
            dest = root / "synth.log"
            ev = synth_log_from(run, dest)
            self.assertTrue(ev["has_terminal_error"])
            self.assertIn("exceeds SYNTH_MEMORY_MAX_BITS", dest.read_text())

    def test_flow_log_winner_keeps_the_stage_log_context(self) -> None:
        # flow.log is the only log carrying a recognised terminal error, so it
        # wins — but the yosys stage log's content must survive alongside it.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = _run_dir(root, {"1_2_yosys.log": "Executing ABC pass.\nunrecognised bail-out\n"},
                           flow_log="make: *** [synth] Error 1\n")
            dest = root / "synth.log"
            ev = synth_log_from(run, dest)
            self.assertEqual(ev["log_path"], str(run / "flow.log"))
            self.assertEqual(ev["stage_context_from"], "1_2_yosys.log")
            self.assertIn("unrecognised bail-out", dest.read_text())

    def test_no_error_anywhere_is_diagnostic_incomplete_not_low_value(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = _run_dir(root, {"1_2_yosys.log": CANONICALIZE_OK})
            dest = root / "synth.log"
            ev = synth_log_from(run, dest)
            self.assertFalse(ev["has_terminal_error"])
            self.assertEqual(ev["diagnostic"], "diagnostic_incomplete")

    def test_missing_run_dir_writes_empty_and_reports_it(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "synth.log"
            ev = synth_log_from(None, dest)
            self.assertEqual(dest.read_text(), "")
            self.assertEqual(ev["diagnostic"], "no_run_dir")

    def test_summary_surfaces_the_error_not_the_trailing_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "synth.log"
            log.write_text(MEM_ABORT + "\n".join(f"stat line {i}" for i in range(40)),
                           encoding="utf-8")
            self.assertIn("exceeds SYNTH_MEMORY_MAX_BITS", summarize_synth_failure(log))


class MemoryEvidenceTests(unittest.TestCase):
    def test_observed_and_cap_are_named_separately(self) -> None:
        ev = memory_limit_evidence(MEM_ABORT)
        self.assertEqual(ev["observed_bits"], 25856)
        self.assertEqual(ev["configured_cap_bits"], 4096)
        self.assertEqual(ev["next_cap_bits"], 32768)     # bounded tier above 25856
        self.assertFalse(ev["implausible"])

    def test_non_memory_text_yields_none(self) -> None:
        self.assertIsNone(memory_limit_evidence("ERROR: syntax error near 'foo'"))


class TerminalClassificationTests(unittest.TestCase):
    def test_memory_limit_is_a_retry_not_a_permanent_exclusion(self) -> None:
        bucket, reason = classify("/src/mor1kx.v", MEM_ABORT)
        self.assertEqual((bucket, reason), ("retry", "memory_limit"))

    def test_structured_memory_marker_also_classifies(self) -> None:
        note = "synthesis_failed | memory_limit observed_bits=65536 cap_bits=4096 next_cap_bits=131072"
        self.assertEqual(classify("/src/audio.v", note), ("retry", "memory_limit"))

    def test_missing_slang_plugin_is_deferred_not_low_value(self) -> None:
        note = "ERROR: Can't load module `slang.so': No such file | frontend_tool_unavailable=slang"
        self.assertEqual(classify("/src/apb_gpio.sv", note),
                         ("defer", "frontend_tool_unavailable"))

    def test_dialect_incompatibility_is_deferred_not_low_value(self) -> None:
        note = "ERROR: syntax error, unexpected TOK_INT near port `int'"
        self.assertEqual(classify("/src/wb_dma_top.v", note),
                         ("defer", "tool_compatibility"))

    def test_absent_evidence_never_yields_a_terminal_verdict(self) -> None:
        self.assertEqual(classify("/src/x.v", "synthesis_failed | diagnostic_incomplete"),
                         ("retry", "diagnostic_incomplete"))

    def test_a_genuine_source_failure_is_still_excluded(self) -> None:
        bucket, reason = classify("/src/junk.v", "ERROR: syntax error near 'endmodule'")
        self.assertEqual((bucket, reason), ("exclude", "low_value_failure"))


if __name__ == "__main__":
    unittest.main()
