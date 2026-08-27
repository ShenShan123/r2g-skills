"""Icarus oracle (design doc 26 Phase 10, 12 evidence tier).

Compiles + simulates a testbench with iverilog/vvp and returns a VerifierSnapshot-
shaped verdict. ``$fatal`` in a testbench makes vvp exit non-zero, so ``pass`` is
the exit code — robust across testbench styles.

Available on this machine (iverilog/vvp in /usr/bin); graceful (``available``
False) when missing so unit tests never require the toolchain.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ICARUS_VERSION = "icarus-oracle-v0.2"
RTL_OBLIGATIONS = ("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS",
                   "RTL_COMPILE_PASS")


class IcarusOracle:
    def __init__(self, iverilog: str | None = None, vvp: str | None = None,
                 timeout: int = 60):
        self.iverilog = (iverilog or os.environ.get("R2G_IVERILOG")
                         or shutil.which("iverilog"))
        self.vvp = vvp or os.environ.get("R2G_VVP") or shutil.which("vvp")
        self.timeout = timeout
        self.available = bool(self.iverilog and self.vvp)

    def run_test(self, rtl_files: list[Path], tb: Path, *,
                 kind: str = "target") -> dict:
        """Compile + run one testbench. ``kind`` = target | regression."""
        if not self.available:
            return {"verdict": "UNKNOWN", "oracle_type": "UNKNOWN",
                    "reason": "iverilog/vvp not available", "output": "",
                    "compile_verdict": "UNKNOWN", "kind": kind}
        work = Path(tempfile.mkdtemp(prefix="tehm_icarus_"))
        sim = work / "sim"
        compile_ok = self._compile(rtl_files, tb, sim)
        if not compile_ok:
            return {"verdict": "FAIL", "oracle_type": "COMPILE",
                    "reason": "compile failed", "output": self._last_output,
                    "compile_verdict": "FAIL", "kind": kind}
        run_ok, output = self._run(sim)
        verdict = "PASS" if run_ok else "FAIL"
        return {
            "verdict": verdict,
            "oracle_type": ("REGRESSION" if kind == "regression"
                            else "TARGET_TEST"),
            "scope": f"rtl:{kind}",
            "confidence_tier": "R" if kind == "regression" else "T",
            "output": output,
            "reason": "" if run_ok else "testbench $fatal / runtime error",
            "compile_verdict": "PASS",
            "kind": kind,
        }

    def verify(self, rtl_files: list[Path], *, target_tb: Path | None,
               regression_tb: Path | None, obligations: list | None = None
               ) -> dict:
        """Run target + frozen regression; produce a VerifierSnapshot verdict."""
        target = self.run_test(rtl_files, target_tb, kind="target") \
            if target_tb else {"verdict": "UNKNOWN",
                               "compile_verdict": "UNKNOWN", "kind": "target"}
        regression = self.run_test(rtl_files, regression_tb, kind="regression") \
            if regression_tb else {"verdict": "UNKNOWN",
                                   "compile_verdict": "UNKNOWN",
                                   "kind": "regression"}
        target_ok = target.get("verdict") == "PASS"
        regression_ok = regression.get("verdict") == "PASS"

        if target_ok and regression_ok:
            verdict = "PASS"
        elif target.get("verdict") == "FAIL" or regression.get("verdict") == "FAIL":
            verdict = "FAIL"
        else:
            verdict = "UNKNOWN"

        created_regressions = []
        newly_observed = []
        # A missing/unknown regression is an incomplete oracle, not evidence
        # that the target fix created a regression.  Only a definitive FAIL
        # in the frozen regression arm can create that observation.
        if target_ok and regression.get("verdict") == "FAIL":
            created_regressions.append("RTL_FROZEN_REGRESSION_PASS")
        if not target_ok and target.get("verdict") == "FAIL":
            newly_observed.append("RTL_TARGET_TEST_PASS")

        required = tuple(dict.fromkeys(obligations or RTL_OBLIGATIONS))
        evidence = {
            "RTL_TARGET_TEST_PASS": target.get("verdict") != "UNKNOWN",
            "RTL_FROZEN_REGRESSION_PASS": regression.get("verdict") != "UNKNOWN",
            # A compile result is evidence for the compile obligation whether
            # it passed or failed; a missing test arm contributes no compile
            # evidence.  This keeps coverage about checked obligations rather
            # than silently treating an absent arm as checked.
            "RTL_COMPILE_PASS": any(
                run.get("compile_verdict") != "UNKNOWN"
                for run in (target, regression)),
        }
        checked = [o for o in required if evidence.get(o, False)]
        known_refs = []
        if target.get("verdict") != "UNKNOWN":
            known_refs.append("target")
        if regression.get("verdict") != "UNKNOWN":
            known_refs.append("regression")
        oracle_type, confidence_tier = _aggregate_oracle_type(
            target, regression, target_known=bool(known_refs),
            regression_known=regression.get("verdict") != "UNKNOWN")
        return {
            "verdict": verdict,
            "oracle_type": oracle_type,
            "scope": "rtl:target+regression",
            "confidence_tier": confidence_tier,
            "obligation_coverage": len(checked) / len(required) if required else None,
            "oracle_complete": bool(required) and len(checked) == len(required),
            "evidence_refs": known_refs,
            "created_regressions": created_regressions,
            "newly_observed_failures": newly_observed,
            "target": target,
            "regression": regression,
            "extractor_version": ICARUS_VERSION,
        }
    # -- internals ------------------------------------------------------------

    def _compile(self, rtl_files, tb, sim: Path) -> bool:
        proc = subprocess.run(
            [str(self.iverilog), "-g2012", "-o", str(sim),
             *[str(p) for p in rtl_files], str(tb)],
            capture_output=True, text=True, timeout=self.timeout)
        self._last_output = proc.stdout + proc.stderr
        return proc.returncode == 0

    def _run(self, sim: Path) -> tuple[bool, str]:
        proc = subprocess.run([str(self.vvp), str(sim)],
                              capture_output=True, text=True,
                              timeout=self.timeout)
        return proc.returncode == 0, proc.stdout + proc.stderr


def _aggregate_oracle_type(target: dict, regression: dict, *,
                           target_known: bool,
                           regression_known: bool) -> tuple[str, str]:
    """Report the strongest oracle actually exercised by this invocation.

    The aggregate used to claim ``REGRESSION``/tier ``R`` even when only the
    target test (or merely compilation) ran.  That made partial verification
    look equivalent to a complete target+frozen-regression receipt.
    """
    if regression_known and regression.get("oracle_type") == "REGRESSION":
        return "REGRESSION", "R"
    if ((target_known and target.get("oracle_type") == "COMPILE") or
            (regression_known and regression.get("oracle_type") == "COMPILE")):
        return "COMPILE", "H"
    if target_known and target.get("oracle_type") == "TARGET_TEST":
        return "TARGET_TEST", "T"
    if regression_known:
        return str(regression.get("oracle_type", "UNKNOWN")), "H"
    if target_known:
        return str(target.get("oracle_type", "UNKNOWN")), "H"
    return "UNKNOWN", "H"
