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
                allow_unverified_source=False, allow_unready_rtl=False)
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
                      manifest: bool = True, rtl_name: str = "top.v",
                      extra_rtl: dict[str, str] | None = None) -> None:
        """manifest=True stamps a COMPLETE synth-time source_manifest, which is
        what a modern expansion produces. Pass manifest=False for the legacy
        pre-manifest shape, which promotion now blocks (audit P0-R6)."""
        ddir = self.out_root / design
        ddir.mkdir(parents=True, exist_ok=True)
        rtl = self.root / "downloads" / design / rtl_name
        rtl.parent.mkdir(parents=True, exist_ok=True)
        rtl.write_text(rtl_text, encoding="utf-8")
        rtls = [rtl]
        for name, text in (extra_rtl or {}).items():
            rtls.append(rtl.parent / name)
            rtls[-1].parent.mkdir(parents=True, exist_ok=True)
            rtls[-1].write_text(text, encoding="utf-8")
        synth_proj = self.root / "workspace" / "synth_projects" / design / "constraints"
        synth_proj.mkdir(parents=True, exist_ok=True)
        cfg_lines = synth_cfg_lines if synth_cfg_lines is not None else [
            f"export DESIGN_NAME = {top}",
            "export PLATFORM = nangate45",
            "export ABC_AREA = 0",
            "export SYNTH_VARIANT = yosys_abc_area0",
            "export R2G_FLOW_SCOPE = synth_only",
            f"export VERILOG_FILES = {' '.join(str(r) for r in rtls)}",
            f"export VERILOG_INCLUDE_DIRS = {rtl.parent}",
        ]
        (synth_proj / "config.mk").write_text("\n".join(cfg_lines) + "\n",
                                              encoding="utf-8")
        meta = {"design": design, "top": top, "status": status,
                "synth_variant": "yosys_abc_area0", "platform": "nangate45",
                "rtl_files": [str(r) for r in rtls],
                "design_config": str(synth_proj / "config.mk")}
        if manifest:
            meta["source_manifest"] = [
                {"path": str(r),
                 "sha256": hashlib.sha256(r.read_bytes()).hexdigest()} for r in rtls]
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
        # Status refined from the generic "failed" to a specific
        # `rtl_files_unresolved` (2026-07-19 audit P1-N6), matching the
        # established per-cause convention (rtl_bytes_changed_since_synth,
        # source_manifest_incomplete, rejected_unconstrained_clock). Unreachable
        # RTL is now reported as unresolved AFTER the vendored-rtl fallback has
        # also been tried, so this candidate has genuinely nowhere left to look.
        self._mk_candidate("gone", RTL_CLK, top="toy_top")
        (self.root / "downloads" / "gone" / "top.v").unlink()
        res = self._promote("gone")
        self.assertEqual(res["status"], "rtl_files_unresolved")
        self.assertIn("could not be resolved", res["reason"])
        self.assertFalse((self.base / "gone").exists())

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


class SemanticReadinessGate(PromoteFixture):
    """RMD-HO-P0-01: byte provenance proves WHICH RTL we have, never that the RTL
    is a finished design. secworks_sha3 declared itself unusable in its own README
    and synthesized with substantial undriven internal logic — and promoted."""

    SHA3_README = "# sha3\n\n## Status\nNot completed. Does not work. Do. Not. Use.\n"
    UNDRIVEN = "\n".join(
        f"Warning: Wire \\top.\\w{i} is used but has no driver." for i in range(12))

    def _unfinished(self, design: str = "sha3") -> None:
        self._mk_candidate(design, RTL_CLK, top="toy_top")
        # The candidate's own repo declares itself unusable ...
        repo = self.root / "downloads" / design
        (repo / "README.md").write_text(self.SHA3_README, encoding="utf-8")
        # ... and its synthesis log shows material structural incompleteness.
        (self.out_root / design / "synth.log").write_text(self.UNDRIVEN, encoding="utf-8")

    def test_unfinished_rtl_is_blocked_from_normal_promotion(self) -> None:
        self._unfinished()
        res = self._promote("sha3")
        self.assertEqual(res["status"], "rejected_semantic_incomplete", res)
        self.assertEqual(res["rtl_readiness"], "rejected_semantic_incomplete")
        self.assertFalse((self.base / "sha3").exists(),
                         "a rejected candidate must not create a project")

    def test_a_healthy_candidate_still_promotes_and_is_stamped_ready(self) -> None:
        self._mk_candidate("healthy", RTL_CLK, top="toy_top")
        res = self._promote("healthy")
        self.assertEqual(res["status"], "promoted", res)
        self.assertEqual(res["rtl_readiness"], "ready")
        prov = json.loads((self.base / "healthy" / "metadata.json").read_text())
        self.assertEqual(prov["rtl_readiness"], "ready")
        self.assertNotIn("rtl_readiness_override", prov)

    def test_a_bad_readme_alone_is_manual_review_not_rejection(self) -> None:
        self._mk_candidate("wip", RTL_CLK, top="toy_top")
        (self.root / "downloads" / "wip" / "README.md").write_text(
            self.SHA3_README, encoding="utf-8")
        res = self._promote("wip")
        self.assertEqual(res["status"], "manual_review", res)

    def test_operator_override_promotes_but_keeps_the_verdict(self) -> None:
        self._unfinished("forced")
        res = self._promote("forced", allow_unready_rtl=True)
        self.assertEqual(res["status"], "promoted", res)
        prov = json.loads((self.base / "forced" / "metadata.json").read_text())
        self.assertEqual(prov["rtl_readiness"], "rejected_semantic_incomplete",
                         "an override must not launder the verdict")
        self.assertEqual(prov["rtl_readiness_override"],
                         "operator:--allow-unready-rtl")

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


class VendoredBasenameTests(PromoteFixture):
    """A source basename with whitespace must not break the promoted config.mk.

    Latent defect found by cohort B (2026-09-23): vendor_rtl copied RTL flat to
    rtl/<basename> and wrote those paths into VERILOG_FILES, which ORFS splits
    on whitespace, so "my top.v" became two nonexistent inputs and run_orfs.sh
    refused the project (R2G_INPUTS_MISSING, exit 66). Cohort B has 3 such files.
    """

    def test_whitespace_basename_is_vendored_make_safe_with_provenance(self) -> None:
        self._mk_candidate("ws", RTL_CLK, top="toy_top", rtl_name="my top.v")
        res = self._promote("ws")
        self.assertEqual(res["status"], "promoted", res)
        cfg = (self.base / "ws" / "constraints" / "config.mk").read_text()
        files = next(ln for ln in cfg.splitlines()
                     if ln.startswith("export VERILOG_FILES")).split("=", 1)[1].split()
        self.assertTrue(files)
        for f in files:                         # make/ORFS split on whitespace
            self.assertTrue(Path(f).is_file(), f)
        self.assertEqual(Path(files[0]).name, "my_top.v")
        prov = json.loads((self.base / "ws" / "metadata.json").read_text())
        [row] = prov["vendored_rtl"]
        src = self.root / "downloads" / "ws" / "my top.v"
        self.assertEqual(row["source_path"], str(src))
        self.assertEqual(row["vendored_path"], "rtl/my_top.v")
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        self.assertEqual(row["source_sha256"], digest)
        self.assertEqual(row["vendored_sha256"], digest)

    def test_an_include_of_the_original_name_still_resolves(self) -> None:
        top = RTL_CLK.replace("module toy_top", '`include "my defs.v"\nmodule toy_top')
        self._mk_candidate("inc", top, top="toy_top",
                           extra_rtl={"my defs.v": "`define W 4\n"})
        res = self._promote("inc")
        self.assertEqual(res["status"], "promoted", res)
        rtl = self.base / "inc" / "rtl"
        self.assertTrue((rtl / "my_defs.v").is_file())
        self.assertTrue((rtl / "my defs.v").is_file())     # `include by original name
        self.assertEqual((rtl / "my defs.v").read_bytes(), (rtl / "my_defs.v").read_bytes())
        cfg = (self.base / "inc" / "constraints" / "config.mk").read_text()
        self.assertNotIn("my defs.v", cfg)

    def test_a_collision_suffix_never_overwrites_the_first_file(self) -> None:
        # Review finding (2026-09-23): the alias fired on collision renames too,
        # so sub/defs.v (vendored as defs_1.v) was copied over rtl/defs.v. With no
        # include of the shared name, both copies stay intact and unaliased.
        self._mk_candidate("col", RTL_CLK, top="toy_top",
                           extra_rtl={"defs.v": "`define W 4\n",
                                      "sub/defs.v": "`define W 8\n"})
        res = self._promote("col")
        self.assertEqual(res["status"], "promoted", res)
        rtl = self.base / "col" / "rtl"
        self.assertEqual((rtl / "defs.v").read_text(), "`define W 4\n")
        self.assertEqual((rtl / "defs_1.v").read_text(), "`define W 8\n")
        for row in res["vendored_rtl"]:
            self.assertNotIn("include_alias", row)
            self.assertEqual(row["vendored_sha256"], hashlib.sha256(
                (self.base / "col" / row["vendored_path"]).read_bytes()).hexdigest())

class VendoredNameReviewTests(PromoteFixture):
    """Re-review findings on ee57ccb (2026-09-23)."""

    def test_a_sanitized_name_never_takes_a_safe_siblings_name(self) -> None:
        # N1: "a b.v" listed before "a_b.v" became a_b.v and pushed the real a_b.v
        # to a_b_1.v, so `include "a_b.v"` read W 4 while synthesis used W 8.
        top = RTL_CLK.replace("module toy_top", '`include "a_b.v"\nmodule toy_top')
        self._mk_candidate("s", top, top="toy_top",
                           extra_rtl={"a b.v": "`define W 4\n", "a_b.v": "`define W 8\n"})
        res = self._promote("s")
        self.assertEqual(res["status"], "promoted", res)
        rtl = self.base / "s" / "rtl"
        self.assertEqual((rtl / "a_b.v").read_text(), "`define W 8\n")
        by_src = {Path(r["source_path"]).name: r["vendored_path"] for r in res["vendored_rtl"]}
        self.assertEqual(by_src["a_b.v"], "rtl/a_b.v")
        self.assertEqual(by_src["a b.v"], "rtl/a_b_1.v")

    def test_force_repromotion_is_not_blocked_by_a_stale_alias(self) -> None:
        # N2: --force leaves rtl/ in place, so last run's alias looked like a conflict.
        top = RTL_CLK.replace("module toy_top", '`include "my defs.v"\nmodule toy_top')
        self._mk_candidate("f", top, top="toy_top", extra_rtl={"my defs.v": "`define W 4\n"})
        self.assertEqual(self._promote("f")["status"], "promoted")
        import shutil
        shutil.rmtree(self.out_root)
        self.out_root.mkdir()
        self._mk_candidate("f", top, top="toy_top", extra_rtl={"my defs.v": "`define W 8\n"})
        res = self._promote("f", force=True)
        self.assertEqual(res["status"], "promoted", res)
        self.assertEqual((self.base / "f" / "rtl" / "my defs.v").read_text(), "`define W 8\n")

    def test_a_header_cannot_overwrite_a_vendored_source(self) -> None:
        # P3: the closure header "my_defs.v" must not replace the source vendored
        # under that name ("my defs.v" -> my_defs.v) with different bytes.
        import promote.promote_candidates as pc
        rtl_dir = self.root / "p" / "rtl"
        src = self.root / "src" / "my defs.v"
        src.parent.mkdir(parents=True)
        src.write_text("`define W 4\n")
        vendored = pc.vendor_rtl([src], rtl_dir)
        hdr = self.root / "hdr" / "my_defs.v"
        hdr.parent.mkdir(parents=True)
        hdr.write_text("`define W 8\n")
        _, unresolved = pc.vendor_headers([{"path": str(hdr)}], self.root / "cand", rtl_dir,
                                          sources=vendored)
        self.assertTrue(any("include_alias_conflict" in u for u in unresolved), unresolved)
        self.assertEqual((rtl_dir / "my_defs.v").read_text(), "`define W 4\n")


class VendoredNameReReviewTests(PromoteFixture):
    """Re-review findings on 3502e05 (2026-09-23)."""

    def test_an_include_of_a_duplicated_basename_fails_loud(self) -> None:
        # Legacy wrong-circuit path: `include "defs.v"` with two different defs.v
        # sources read whichever was vendored first. Refuse instead.
        top = RTL_CLK.replace("module toy_top", '`include "defs.v"\nmodule toy_top')
        self._mk_candidate("amb", top, top="toy_top",
                           extra_rtl={"defs.v": "`define W 4\n",
                                      "sub/defs.v": "`define W 8\n"})
        res = self._promote("amb")
        self.assertEqual(res["status"], "include_ambiguous", res)
        self.assertIn("defs.v", res["reason"])

    def test_force_removes_stale_rtl_from_an_earlier_promotion(self) -> None:
        self._mk_candidate("st", RTL_CLK, top="toy_top", extra_rtl={"old.vh": "`define X\n"})
        self.assertEqual(self._promote("st")["status"], "promoted")
        import shutil
        shutil.rmtree(self.out_root)
        self.out_root.mkdir()
        self._mk_candidate("st", RTL_CLK, top="toy_top")        # old.vh no longer a source
        res = self._promote("st", force=True)
        self.assertEqual(res["status"], "promoted", res)
        rtl = self.base / "st" / "rtl"
        self.assertFalse((rtl / "old.vh").exists())             # off the include path
        [(was, now)] = res["stale_rtl_moved"].items()
        self.assertEqual(was, "rtl/old.vh")
        # Parked, not deleted (a hand-added file survives), in a subdirectory that
        # the flat rtl/ include path never searches.
        self.assertTrue(now.startswith("rtl/.stale/"))
        self.assertEqual((self.base / "st" / now).read_text(), "`define X\n")
        self.assertEqual(sorted(f.name for f in rtl.iterdir() if f.is_file()), ["top.v"])

    def test_a_failed_force_repromotion_leaves_no_runnable_project(self) -> None:
        # F1: after init_project, an early return (here include_ambiguous) left the
        # old config.mk, a mixed rtl/ and a metadata.json without promoted_from.
        top = RTL_CLK.replace("module toy_top", '`include "defs.v"\nmodule toy_top')
        self._mk_candidate("f", top, top="toy_top", extra_rtl={"defs.v": "`define W 4\n"})
        self.assertEqual(self._promote("f")["status"], "promoted")
        import shutil
        shutil.rmtree(self.out_root)
        self.out_root.mkdir()
        shutil.rmtree(self.root / "downloads")
        self._mk_candidate("f", top, top="toy_top",
                           extra_rtl={"defs.v": "`define W 8\n", "sub/defs.v": "`define W 9\n"})
        res = self._promote("f", force=True)
        self.assertEqual(res["status"], "include_ambiguous", res)
        p = self.base / "f"
        self.assertFalse((p / "constraints" / "config.mk").exists())
        self.assertTrue((p / "constraints" / "config.mk.invalid").exists())
        meta = json.loads((p / "metadata.json").read_text())
        self.assertEqual(meta["status"], "include_ambiguous")
        self.assertIn("corpus/f", meta["promoted_from"])
        self.assertIn("include_ambiguous", meta["reason"])

    def test_a_suffixed_source_never_takes_a_header_name(self) -> None:
        # R2: a second defs.v suffixed to defs_1.v blocked (or shadowed) a real
        # closure header called defs_1.v.
        import promote.promote_candidates as pc
        a = self.root / "a" / "defs.v"
        b = self.root / "b" / "defs.v"
        for f, w in ((a, 4), (b, 8)):
            f.parent.mkdir(parents=True)
            f.write_text(f"`define W {w}\n")
        rtl_dir = self.root / "p" / "rtl"
        vendored = pc.vendor_rtl([a, b], rtl_dir, reserved_names={"defs_1.v"})
        self.assertNotIn("defs_1.v", [v.name for v in vendored])
        hdr = self.root / "h" / "defs_1.v"
        hdr.parent.mkdir(parents=True)
        hdr.write_text("`define H 1\n")
        got, unresolved = pc.vendor_headers([{"path": str(hdr)}], self.root / "c", rtl_dir,
                                            sources=vendored)
        self.assertEqual(unresolved, [])
        self.assertEqual((rtl_dir / "defs_1.v").read_text(), "`define H 1\n")


class VendoredNameRound4Tests(PromoteFixture):
    """Round-4 re-review findings on 2a5ef51 (2026-09-23)."""

    def _reset(self, design: str) -> None:
        import shutil
        shutil.rmtree(self.out_root)
        self.out_root.mkdir()
        shutil.rmtree(self.root / "downloads")

    def test_a_plain_rerun_after_a_failed_force_parks_the_leftovers(self) -> None:
        # G1: the failed --force parked config.mk, so a plain re-run passed the
        # existing-project guard, and parking only ran under --force: the
        # rejected defs_1.v and the stale old.v stayed on the include path.
        top = RTL_CLK.replace("module toy_top", '`include "defs.v"\nmodule toy_top')
        self._mk_candidate("f", top, top="toy_top",
                           extra_rtl={"defs.v": "`define W 4\n", "old.v": "// stale\n"})
        self.assertEqual(self._promote("f")["status"], "promoted")
        self._reset("f")
        self._mk_candidate("f", top, top="toy_top",
                           extra_rtl={"defs.v": "`define W 8\n", "sub/defs.v": "`define W 9\n"})
        self.assertEqual(self._promote("f", force=True)["status"], "include_ambiguous")
        self._reset("f")
        self._mk_candidate("f", top, top="toy_top", extra_rtl={"defs.v": "`define W 8\n"})
        res = self._promote("f")
        self.assertEqual(res["status"], "promoted", res)
        rtl = self.base / "f" / "rtl"
        self.assertEqual(sorted(f.name for f in rtl.iterdir() if f.is_file()),
                         ["defs.v", "top.v"])

    def test_an_exception_after_init_leaves_no_runnable_project(self) -> None:
        # G2: an exception after init_project skipped _fail_after_init.
        from unittest import mock
        import promote.promote_candidates as pc
        self._mk_candidate("x", RTL_CLK, top="toy_top")
        self.assertEqual(self._promote("x")["status"], "promoted")
        with mock.patch.object(pc, "vendor_rtl", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self._promote("x", force=True)
        p = self.base / "x"
        self.assertFalse((p / "constraints" / "config.mk").exists())
        meta = json.loads((p / "metadata.json").read_text())
        self.assertEqual(meta["status"], "failed")
        self.assertIn("disk full", meta["reason"])

    def test_two_parks_in_one_second_never_overwrite(self) -> None:
        # G3: runs within one second shared rtl/.stale/<ts>/ and overwrote.
        from unittest import mock
        import promote.promote_candidates as pc
        self._mk_candidate("t", RTL_CLK, top="toy_top")
        self.assertEqual(self._promote("t")["status"], "promoted")
        rtl = self.base / "t" / "rtl"
        with mock.patch.object(pc, "now_iso", return_value="2026-09-23T12:00:00"):
            (rtl / "old.v").write_text("// first\n")
            first = self._promote("t", force=True)["stale_rtl_moved"]["rtl/old.v"]
            (rtl / "old.v").write_text("// second\n")
            second = self._promote("t", force=True)["stale_rtl_moved"]["rtl/old.v"]
        self.assertNotEqual(first, second)
        self.assertEqual((self.base / "t" / first).read_text(), "// first\n")
        self.assertEqual((self.base / "t" / second).read_text(), "// second\n")
