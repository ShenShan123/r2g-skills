"""One-click promote: synth-proven corpus candidate -> signoff-loop full-flow project.

Covers the scoped-reuse contract of scripts/promote/promote_candidates.py:
gate on corpus success, vendor the proven RTL, carry the proven synth knobs,
ADD the floorplan directive, DROP the synth_only scope marker, detect the
clock port (or fall back to a virtual clock), and run validate_config.py as
the readiness gate.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from promote.promote_candidates import (  # noqa: E402
    detect_clock_port,
    load_index,
    promote_one,
)

RTL_CLK = """module toy_top (clk, rst_n, d_in, d_out);
input clk, rst_n;
input [3:0] d_in;
output reg [3:0] d_out;
always @(posedge clk or negedge rst_n)
  if (!rst_n) d_out <= 4'd0;
  else d_out <= d_in;
endmodule
"""

RTL_COMB = """module comb_top (a, b, y);
input [3:0] a, b;
output [3:0] y;
assign y = a & b;
endmodule
"""


def _args(**over) -> argparse.Namespace:
    base = dict(designs=[], all=False, out_root=None, base_dir=None, platform="",
                clock_port="", clock_period=10.0, core_utilization=30,
                place_density=0.20, require_publish_eligible=False,
                publish_eligible_csv=None, force=False, run=False, dry_run=False,
                allow_unverified_source=False)
    base.update(over)
    return argparse.Namespace(**base)


class PromoteFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.out_root = self.root / "corpus"
        self.base = self.root / "design_cases"
        self.out_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mk_candidate(self, design: str, rtl_text: str, *, top: str,
                      status: str = "success", extra_meta: dict | None = None,
                      synth_cfg_lines: list[str] | None = None,
                      manifest: bool = True) -> None:
        """manifest=True stamps a COMPLETE synth-time source_manifest, which is
        what a modern expansion produces. Pass manifest=False for the legacy
        pre-manifest shape, which promotion now blocks (audit P0-R6)."""
        ddir = self.out_root / design
        ddir.mkdir(parents=True, exist_ok=True)
        rtl = self.root / "downloads" / design / "top.v"
        rtl.parent.mkdir(parents=True, exist_ok=True)
        rtl.write_text(rtl_text, encoding="utf-8")
        synth_proj = self.root / "workspace" / "synth_projects" / design / "constraints"
        synth_proj.mkdir(parents=True, exist_ok=True)
        cfg_lines = synth_cfg_lines if synth_cfg_lines is not None else [
            f"export DESIGN_NAME = {top}",
            "export PLATFORM = nangate45",
            "export ABC_AREA = 0",
            "export SYNTH_VARIANT = yosys_abc_area0",
            "export R2G_FLOW_SCOPE = synth_only",
            f"export VERILOG_FILES = {rtl}",
            f"export VERILOG_INCLUDE_DIRS = {rtl.parent}",
        ]
        (synth_proj / "config.mk").write_text("\n".join(cfg_lines) + "\n",
                                              encoding="utf-8")
        meta = {"design": design, "top": top, "status": status,
                "synth_variant": "yosys_abc_area0", "platform": "nangate45",
                "rtl_files": [str(rtl)],
                "design_config": str(synth_proj / "config.mk")}
        if manifest:
            meta["source_manifest"] = [
                {"path": str(rtl),
                 "sha256": hashlib.sha256(rtl.read_bytes()).hexdigest()}]
        meta.update(extra_meta or {})
        (ddir / "design_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        with open(self.out_root / "index.csv", "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["design", "top", "status"])
            if f.tell() == 0:
                w.writeheader()
            w.writerow({"design": design, "top": top, "status": status})

    def _promote(self, design: str, **over) -> dict:
        index = load_index(self.out_root)
        return promote_one(design, out_root=self.out_root, base_dir=self.base,
                           args=_args(**over), index_row=index.get(design))


class PromoteTests(PromoteFixture):
    def test_success_candidate_promotes_and_validates(self) -> None:
        self._mk_candidate("toy", RTL_CLK, top="toy_top",
                           extra_meta={"synth_memory_max_bits": 65536})
        res = self._promote("toy")
        self.assertEqual(res["status"], "promoted", res)
        cfg = (self.base / "toy" / "constraints" / "config.mk").read_text()
        self.assertIn("export DESIGN_NAME = toy_top", cfg)
        self.assertIn("export CORE_UTILIZATION = 30", cfg)
        self.assertIn("export PLACE_DENSITY_LB_ADDON = 0.20", cfg)
        self.assertIn("export SYNTH_MEMORY_MAX_BITS = 65536", cfg)
        self.assertIn("export ABC_AREA = 0", cfg)          # carried, not template's 1
        self.assertNotIn("R2G_FLOW_SCOPE", cfg)            # full-flow: scope DROPPED
        # RTL vendored + absolute path in VERILOG_FILES
        vendored = self.base / "toy" / "rtl" / "top.v"
        self.assertTrue(vendored.is_file())
        self.assertIn(str(vendored), cfg)
        sdc = (self.base / "toy" / "constraints" / "constraint.sdc").read_text()
        self.assertIn("set clk_port_name clk", sdc)
        self.assertEqual(res["validate_rc"], 0, res.get("validate_tail"))
        prov = json.loads((self.base / "toy" / "metadata.json").read_text())
        self.assertEqual(prov["status"], "promoted")
        self.assertIn("corpus/toy", prov["promoted_from"])

    def test_non_success_is_refused(self) -> None:
        self._mk_candidate("bad", RTL_CLK, top="toy_top", status="synth_failed")
        res = self._promote("bad")
        self.assertEqual(res["status"], "failed")
        self.assertIn("synth_failed", res["reason"])
        self.assertFalse((self.base / "bad").exists())

    def test_missing_rtl_is_refused(self) -> None:
        self._mk_candidate("gone", RTL_CLK, top="toy_top")
        (self.root / "downloads" / "gone" / "top.v").unlink()
        res = self._promote("gone")
        self.assertEqual(res["status"], "failed")
        self.assertIn("missing on disk", res["reason"])

    def test_combinational_gets_virtual_clock(self) -> None:
        self._mk_candidate("comb", RTL_COMB, top="comb_top")
        res = self._promote("comb")
        sdc = (self.base / "comb" / "constraints" / "constraint.sdc").read_text()
        self.assertIn("virtual_clk", sdc)
        self.assertEqual(res["clock_port"], "(virtual)")

    def test_existing_project_needs_force(self) -> None:
        self._mk_candidate("dup", RTL_CLK, top="toy_top")
        first = self._promote("dup")
        self.assertEqual(first["status"], "promoted")
        second = self._promote("dup")
        self.assertEqual(second["status"], "failed")
        self.assertIn("--force", second["reason"])
        third = self._promote("dup", force=True)
        self.assertEqual(third["status"], "promoted")

    def test_dry_run_touches_nothing(self) -> None:
        self._mk_candidate("dry", RTL_CLK, top="toy_top")
        res = self._promote("dry", dry_run=True)
        self.assertEqual(res["status"], "would_promote")
        self.assertFalse((self.base / "dry").exists())

    def test_run_flow_failure_updates_on_disk_manifest(self) -> None:
        """--run flow failure must re-dump promote.json/metadata.json so the
        ON-DISK status reflects promoted_flow_failed, not a stale 'promoted'
        (failure-patterns.md #38 / codex #2)."""
        from unittest import mock
        import promote.promote_candidates as pc
        self._mk_candidate("runfail", RTL_CLK, top="toy_top")
        # Real subprocess, trivial stub that fails — only run_orfs is redirected
        # (mocking subprocess.run globally would break init_project's skeleton).
        stub = self.root / "fake_run_orfs.sh"
        stub.write_text("#!/usr/bin/env bash\nexit 1\n")
        stub.chmod(0o755)
        with mock.patch.object(pc, "run_orfs_script", return_value=stub):
            res = self._promote("runfail", run=True)
        self.assertEqual(res["status"], "promoted_flow_failed")
        prov = json.loads((self.base / "runfail" / "metadata.json").read_text())
        self.assertEqual(prov["status"], "promoted_flow_failed")
        pj = json.loads((self.base / "runfail" / "reports" / "promote.json").read_text())
        self.assertEqual(pj["status"], "promoted_flow_failed")
        self.assertEqual(pj["orfs_rc"], 1)


class SourceProofGateTests(PromoteFixture):
    """Source-byte provenance must be an ENFORCED gate, not a descriptive stamp
    (2026-07-19 audit P0-R4 / P0-R6, failure-patterns #52)."""

    def _multi_file_candidate(self, design: str, *, covered: int) -> None:
        """A 5-file candidate whose manifest covers only `covered` of them —
        the real eth_rxethmac shape from the report."""
        ddir = self.out_root / design
        ddir.mkdir(parents=True, exist_ok=True)
        src = self.root / "downloads" / design
        src.mkdir(parents=True, exist_ok=True)
        rtl_files = []
        for i in range(5):
            p = src / f"m{i}.v"
            # m0 keeps the real top module; the rest are distinct siblings, so
            # validate_config still resolves DESIGN_NAME.
            p.write_text(RTL_CLK if i == 0
                         else RTL_CLK.replace("toy_top", f"m{i}"), encoding="utf-8")
            rtl_files.append(p)
        synth_proj = self.root / "workspace" / "synth_projects" / design / "constraints"
        synth_proj.mkdir(parents=True, exist_ok=True)
        (synth_proj / "config.mk").write_text("\n".join([
            "export DESIGN_NAME = toy_top", "export PLATFORM = nangate45",
            "export ABC_AREA = 0", "export SYNTH_VARIANT = yosys_abc_area0",
            f"export VERILOG_FILES = {' '.join(str(p) for p in rtl_files)}",
            f"export VERILOG_INCLUDE_DIRS = {src}"]) + "\n", encoding="utf-8")
        meta = {"design": design, "top": "toy_top", "status": "success",
                "synth_variant": "yosys_abc_area0", "platform": "nangate45",
                "rtl_files": [str(p) for p in rtl_files],
                "design_config": str(synth_proj / "config.mk"),
                "source_manifest": [
                    {"path": str(p),
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in rtl_files[:covered]]}
        (ddir / "design_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        with open(self.out_root / "index.csv", "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["design", "top", "status"])
            if f.tell() == 0:
                w.writeheader()
            w.writerow({"design": design, "top": "toy_top", "status": "success"})

    def test_partial_manifest_cannot_claim_verification(self) -> None:
        """5 RTL files, 1 manifest entry: promotion used to return
        source_bytes_verified=true with rtl_file_count=5 — four unverified files
        free to change behind a positive claim of full verification."""
        self._multi_file_candidate("partial", covered=1)
        res = self._promote("partial")
        self.assertEqual(res["status"], "source_manifest_incomplete", res)
        self.assertFalse(res.get("source_bytes_verified"))
        self.assertIn("4 of 5", res["reason"])
        self.assertFalse((self.base / "partial").exists(),
                         "blocked promotion must stop BEFORE project creation")

    def test_complete_manifest_still_promotes(self) -> None:
        self._multi_file_candidate("complete", covered=5)
        res = self._promote("complete")
        self.assertEqual(res["status"], "promoted", res)
        self.assertTrue(res["source_bytes_verified"])

    def test_legacy_candidate_without_manifest_is_blocked(self) -> None:
        """The real legacy picorv32_core shape: no manifest at all. Recording
        source_bytes_verified=false honestly is not the same as enforcing it —
        promotion used to continue into project creation and vendoring."""
        self._mk_candidate("legacy", RTL_CLK, top="toy_top", manifest=False)
        res = self._promote("legacy")
        self.assertEqual(res["status"], "source_manifest_missing", res)
        self.assertFalse(res["source_bytes_verified"])
        self.assertFalse((self.base / "legacy").exists(),
                         "blocked promotion must stop BEFORE project creation")

    def test_operator_override_promotes_but_keeps_the_unverified_stamp(self) -> None:
        """Recovery stays possible — but the downstream manifest must never
        claim a verification that did not happen."""
        self._mk_candidate("legacy2", RTL_CLK, top="toy_top", manifest=False)
        res = self._promote("legacy2", allow_unverified_source=True)
        self.assertEqual(res["status"], "promoted", res)
        self.assertFalse(res["source_bytes_verified"])
        self.assertEqual(res["source_verification_override"],
                         "operator:--allow-unverified-source")
        prov = json.loads((self.base / "legacy2" / "metadata.json").read_text())
        # Assert PRESENCE before value: `.get(..., False)` passed vacuously while
        # metadata.json omitted the key entirely (audit P0-R6 follow-up).
        self.assertIn("source_bytes_verified", prov,
                      "metadata.json must carry the verification contract")
        self.assertFalse(prov["source_bytes_verified"],
                         "override must not launder the unverified stamp")
        self.assertEqual(prov["source_verification_override"],
                         "operator:--allow-unverified-source")

    def test_verified_promotion_stamps_metadata_true(self) -> None:
        self._multi_file_candidate("stamped", covered=5)
        self._promote("stamped")
        prov = json.loads((self.base / "stamped" / "metadata.json").read_text())
        self.assertTrue(prov["source_bytes_verified"])
        self.assertNotIn("source_verification_override", prov)

    def test_null_digest_entry_cannot_launder_a_pass(self) -> None:
        """_source_manifest writes {"sha256": None} for a file unreadable at synth
        time, and the manifest lookup filters those out. Before the coverage
        check that turned a KNOWN-unknown into a silent pass; it must now read as
        uncovered, exactly like an absent entry."""
        self._mk_candidate("nulldig", RTL_CLK, top="toy_top")
        meta_path = self.out_root / "nulldig" / "design_meta.json"
        meta = json.loads(meta_path.read_text())
        meta["source_manifest"] = [{"path": meta["rtl_files"][0], "sha256": None}]
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        res = self._promote("nulldig")
        self.assertEqual(res["status"], "source_manifest_missing", res)
        self.assertFalse((self.base / "nulldig").exists())

    def test_changed_bytes_still_blocked(self) -> None:
        """The pre-existing byte-drift gate must survive the coverage check."""
        self._mk_candidate("drift", RTL_CLK, top="toy_top")
        meta_path = self.out_root / "drift" / "design_meta.json"
        meta = json.loads(meta_path.read_text())
        Path(meta["rtl_files"][0]).write_text(RTL_CLK + "\n// mutated\n",
                                              encoding="utf-8")
        res = self._promote("drift")
        self.assertEqual(res["status"], "rtl_bytes_changed_since_synth", res)


class ClockDetectTests(unittest.TestCase):
    def _one(self, text: str, top: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.v"
            p.write_text(text, encoding="utf-8")
            return detect_clock_port(top, [p])

    def test_non_ansi_input(self) -> None:
        self.assertEqual(self._one(RTL_CLK, "toy_top"), "clk")

    def test_ansi_header(self) -> None:
        text = ("module m (input wire wb_clk_i, input wire [7:0] d,\n"
                "          output wire [7:0] q);\nendmodule\n")
        self.assertEqual(self._one(text, "m"), "wb_clk_i")

    def test_priority_order(self) -> None:
        text = "module m (clk, core_clk);\ninput clk, core_clk;\nendmodule\n"
        self.assertEqual(self._one(text, "m"), "clk")

    def test_no_clock(self) -> None:
        self.assertEqual(self._one(RTL_COMB, "comb_top"), "")

    def test_scans_only_the_named_top(self) -> None:
        text = ("module other (input clk);\nendmodule\n"
                "module m (a, y);\ninput a;\noutput y;\nassign y = a;\nendmodule\n")
        self.assertEqual(self._one(text, "m"), "")


if __name__ == "__main__":
    unittest.main()
