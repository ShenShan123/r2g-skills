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

ICARUS_VERSION = "icarus-oracle-v0.1"
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
                    "reason": "iverilog/vvp not available", "output": ""}
        work = Path(tempfile.mkdtemp(prefix="tehm_icarus_"))
        sim = work / "sim"
        compile_ok = self._compile(rtl_files, tb, sim)
        if not compile_ok:
            return {"verdict": "FAIL", "oracle_type": "COMPILE",
                    "reason": "compile failed", "output": self._last_output}
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
        }

    def verify(self, rtl_files: list[Path], *, target_tb: Path | None,
               regression_tb: Path | None, obligations: list | None = None
               ) -> dict:
        """Run target + frozen regression; produce a VerifierSnapshot verdict."""
        target = self.run_test(rtl_files, target_tb, kind="target") \
            if target_tb else {"verdict": "UNKNOWN"}
        regression = self.run_test(rtl_files, regression_tb, kind="regression") \
            if regression_tb else {"verdict": "UNKNOWN"}
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
        if target_ok and not regression_ok:
            created_regressions.append("RTL_FROZEN_REGRESSION_PASS")
        if not target_ok and target.get("verdict") == "FAIL":
            newly_observed.append("RTL_TARGET_TEST_PASS")

        required = obligations or RTL_OBLIGATIONS
        checked = [o for o in required
                   if o in ("RTL_COMPILE_PASS",) or target.get("verdict") != "UNKNOWN"
                   or regression.get("verdict") != "UNKNOWN"]
        return {
            "verdict": verdict,
            "oracle_type": "REGRESSION",
            "scope": "rtl:target+regression",
            "confidence_tier": "T",
            "obligation_coverage": len(checked) / len(required) if required else None,
            "evidence_refs": [r for r in ("target", "regression")
                              if target.get("verdict") != "UNKNOWN"
                              or regression.get("verdict") != "UNKNOWN"],
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
