"""Capability-aware frontend routing (RMD-HO-P1-03, held-out V3 P1-HO-03).

The expander wrote `SYNTH_HDL_FRONTEND = slang` for ANY bundle containing a .sv
file, with no check that the installed Yosys could load the plugin. On a host
without slang.so that guarantees a synth failure, and the terminal taxonomy then
blamed the RTL (`low_value_failure`) for a missing TOOL.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from common import frontend_capability as fc  # noqa: E402
from execute.expand_candidates import write_project  # noqa: E402


class _Probe(unittest.TestCase):
    def setUp(self) -> None:
        fc._cache.clear()
        fc._record.clear()
        self.addCleanup(fc._cache.clear)
        self.addCleanup(fc._record.clear)

    def _stub(self, rc: int, out: str = "") -> None:
        self._orig_run = fc._run
        self._orig_which = fc.shutil.which
        fc._run = lambda cmd: (rc, out)                       # type: ignore[assignment]
        fc.shutil.which = lambda name: "/usr/bin/yosys"        # type: ignore[assignment]
        self.addCleanup(lambda: setattr(fc, "_run", self._orig_run))
        self.addCleanup(lambda: setattr(fc.shutil, "which", self._orig_which))


class FrontendSelectionTests(_Probe):
    def test_missing_plugin_is_never_selected(self) -> None:
        self._stub(1, "ERROR: Can't load module `slang.so'")
        chosen, rec = fc.select_frontend(None, needs_sv=True)
        self.assertIsNone(chosen)
        self.assertFalse(rec["available"])
        self.assertEqual(rec["frontend"], "slang")

    def test_present_plugin_is_selected(self) -> None:
        self._stub(0)
        chosen, rec = fc.select_frontend(None, needs_sv=True)
        self.assertEqual(chosen, "slang")
        self.assertTrue(rec["available"])

    def test_explicit_request_still_goes_through_the_canary(self) -> None:
        self._stub(1, "unable to load plugin")
        chosen, _ = fc.select_frontend("slang", needs_sv=False)
        self.assertIsNone(chosen)

    def test_plain_verilog_bundle_uses_the_default_frontend(self) -> None:
        self._stub(0)
        chosen, rec = fc.select_frontend(None, needs_sv=False)
        self.assertIsNone(chosen)
        self.assertIsNone(rec["frontend"])

    def test_probe_result_is_cached_not_re_run(self) -> None:
        self._stub(0)
        fc.select_frontend(None, needs_sv=True)
        calls = []
        fc._run = lambda cmd: (calls.append(cmd), (1, "changed"))[1]  # type: ignore[assignment]
        self.assertTrue(fc.frontend_available("slang"))
        self.assertEqual(calls, [])


class ConfigEmissionTests(_Probe):
    def _write(self, tmp: Path, suffix: str) -> str:
        src = tmp / f"design{suffix}"
        src.write_text("module design(input clk); endmodule\n", encoding="utf-8")
        project = write_project(tmp / "projects", "design", "design", "yosys_abc_area0",
                                [src], tmp, [], "", None, None, None)
        return (project / "constraints" / "config.mk").read_text(encoding="utf-8")

    def test_sv_bundle_omits_slang_when_the_plugin_is_absent(self) -> None:
        self._stub(1, "ERROR: Can't load module `slang.so'")
        with tempfile.TemporaryDirectory() as td:
            self.assertNotIn("SYNTH_HDL_FRONTEND", self._write(Path(td), ".sv"))

    def test_sv_bundle_keeps_slang_when_the_plugin_is_present(self) -> None:
        self._stub(0)
        with tempfile.TemporaryDirectory() as td:
            self.assertIn("SYNTH_HDL_FRONTEND = slang", self._write(Path(td), ".sv"))


if __name__ == "__main__":
    unittest.main()
