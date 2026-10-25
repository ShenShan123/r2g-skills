"""Profile-bound Yosys equivalence oracle for RTL candidates.

The oracle is independent of the model proposal.  A different compatibility
profile, missing top module, unavailable Yosys, or an unproven equivalence
result is ``FAIL``/``UNKNOWN`` and cannot satisfy a promotion gate.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from tehm.rtl.compatibility import profile_for_action


EQUIVALENCE_VERSION = "yosys-equivalence-oracle-v1"


class YosysEquivalenceOracle:
    def __init__(self, yosys: str | None = None, timeout: int = 120):
        self.yosys = yosys or os.environ.get("R2G_YOSYS") or shutil.which("yosys")
        self.timeout = int(timeout)
        self.available = bool(self.yosys)

    def verify(self, *, reference_files: list[Path], candidate_files: list[Path],
               reference_top: str, candidate_top: str,
               reference_profile: str, candidate_profile: str) -> dict:
        """Run a structural equivalence proof, failing closed on ambiguity."""
        base = {"version": EQUIVALENCE_VERSION, "oracle_type": "YOSYS_EQUIV",
                "reference_top": reference_top, "candidate_top": candidate_top,
                "reference_profile": reference_profile,
                "candidate_profile": candidate_profile}
        if not reference_profile or reference_profile != candidate_profile:
            return {**base, "verdict": "FAIL", "reason": "compatibility_profile_mismatch"}
        refs = [Path(path).resolve() for path in reference_files]
        cands = [Path(path).resolve() for path in candidate_files]
        if (not refs or not cands or not all(path.is_file() for path in (*refs, *cands))):
            return {**base, "verdict": "UNKNOWN", "reason": "rtl_evidence_missing"}
        reference_digest = _source_set_digest(refs)
        candidate_digest = _source_set_digest(cands)
        if (reference_top == candidate_top and reference_digest == candidate_digest):
            # A physical CONFIG_DELTA does not edit RTL.  Byte identity of the
            # complete source sets is a stronger equivalence proof than asking
            # a bounded SAT engine to rediscover equality of a large sequential
            # design.  Non-identical RTL still takes the executable Yosys path
            # below and must be proven; no hash similarity is accepted.
            proof = (reference_top + ":" + reference_digest).encode()
            return {**base, "verdict": "PASS", "reason": "",
                    "proof_type": "CRYPTOGRAPHIC_SOURCE_IDENTITY_V1",
                    "reference_source_sha256": reference_digest,
                    "candidate_source_sha256": candidate_digest,
                    "returncode": 0,
                    "output_sha256": hashlib.sha256(proof).hexdigest(),
                    "output_tail": "byte-identical complete RTL source sets"}
        if not self.available:
            return {**base, "verdict": "UNKNOWN", "reason": "yosys_unavailable"}
        ref_top = _identifier(reference_top)
        cand_top = _identifier(candidate_top)
        script = "\n".join([
            "read_verilog -sv " + " ".join(_quote(path) for path in refs),
            # Normalize and flatten each side in a separate Yosys design.  The
            # previous implementation renamed the reference top in-place and
            # then re-read a candidate with the same module name; Yosys kept
            # the old module set and reported ``No top module found`` even for
            # byte-identical RTL.  Stash/copy is the standard fail-closed flow
            # for same-top reference/candidate comparisons.
            f"prep -top {ref_top} -flatten",
            f"rename {ref_top} gold",
            "design -stash gold_design",
            "read_verilog -sv " + " ".join(_quote(path) for path in cands),
            f"prep -top {cand_top} -flatten",
            f"rename {cand_top} gate",
            "design -stash gate_design",
            "design -reset",
            "design -copy-from gold_design -as gold gold",
            "design -copy-from gate_design -as gate gate",
            "equiv_make gold gate equiv",
            "prep -top equiv",
            # ``equiv_simple`` alone only proves combinational cones.  Real RTL
            # tops contain state, so first align structurally identical cones,
            # then use bounded sequential SAT plus induction.  An unproven cell
            # remains a hard failure at ``equiv_status -assert``.
            "equiv_struct",
            "equiv_simple -undef -seq 5",
            "equiv_induct -undef -seq 5",
            "equiv_status -assert",
        ])
        try:
            proc = subprocess.run([str(self.yosys), "-p", script],
                                  capture_output=True, text=True,
                                  timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {**base, "verdict": "UNKNOWN", "reason": str(exc)}
        output = proc.stdout + proc.stderr
        return {**base, "verdict": "PASS" if proc.returncode == 0 else "FAIL",
                "reason": "" if proc.returncode == 0 else "equivalence_not_proven",
                "returncode": proc.returncode,
                "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                "output_tail": output[-4000:]}


def verify_profile_equivalence(*, reference_files, candidate_files,
                              reference_top: str, candidate_top: str,
                              reference_action: dict, candidate_action: dict,
                              yosys: str | None = None, timeout: int = 120) -> dict:
    """Derive profiles from typed action payloads before invoking Yosys."""
    try:
        reference_profile = profile_for_action(reference_action)
        candidate_profile = profile_for_action(candidate_action)
    except (TypeError, ValueError) as exc:
        return {"version": EQUIVALENCE_VERSION, "verdict": "FAIL",
                "reason": f"invalid_compatibility_profile:{exc}"}
    return YosysEquivalenceOracle(yosys=yosys, timeout=timeout).verify(
        reference_files=list(reference_files), candidate_files=list(candidate_files),
        reference_top=reference_top, candidate_top=candidate_top,
        reference_profile=reference_profile, candidate_profile=candidate_profile)


def _quote(path: Path) -> str:
    # Yosys' ``-p`` interpreter is not a shell: shell quotes become literal
    # filename characters.  Escape spaces for the command parser instead.
    return str(path).replace(" ", "\\ ")


def _identifier(value: str) -> str:
    """Return a conservative Yosys identifier or fail before running a script."""
    value = str(value)
    if not value or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_$" for ch in value):
        raise ValueError(f"unsupported Yosys top identifier: {value!r}")
    return value


def _source_set_digest(paths: list[Path]) -> str:
    payload = []
    for path in paths:
        data = path.read_bytes()
        payload.append({"name": path.name,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data)})
    return hashlib.sha256(
        repr(sorted(payload, key=lambda row: (row["name"], row["sha256"]))).encode()
    ).hexdigest()
