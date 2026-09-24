"""Cold and in-memory tamper checks for the R5 axis_adapter qualification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from tehm.evaluation import research_r5_axis_adapter_q_probe as probe


def rejects(operation, reason: str) -> None:
    try:
        operation()
    except ValueError as error:
        if reason not in str(error):
            raise AssertionError(f"unexpected rejection: {error}") from error
    else:
        raise AssertionError(f"tampered evidence accepted: {reason}")


def run(receipt: Path, corpus: Path) -> dict:
    result = probe.verify(receipt, corpus)
    assert result["valid"] is True and result["sensitivity"] == "DETECTED"
    assert result["clean"]["verdict"] == "PASS"
    assert result["fault"]["verdict"] == "FAIL"

    original_read = Path.read_bytes
    work = receipt.parent

    def altered(target: Path):
        def read(path: Path) -> bytes:
            payload = original_read(path)
            return payload + b"x" if path == target else payload
        return read

    fault_source = work / "fault/stage/rtl/axis_adapter.v"
    with patch.object(Path, "read_bytes", altered(fault_source)):
        rejects(lambda: probe.verify(receipt, corpus), "staged input drift")

    fault_junit = list((work / "fault/stage/tb/axis_adapter/sim_build/test_axis_register-8-16")
                       .glob("*_results.xml"))
    assert len(fault_junit) == 1
    with patch.object(Path, "read_bytes", altered(fault_junit[0])):
        rejects(lambda: probe.verify(receipt, corpus), "artifact drift")

    fault_vvp = list((work / "fault/stage/tb/axis_adapter/sim_build/test_axis_register-8-16")
                     .glob("*.vvp"))
    assert len(fault_vvp) == 1
    with patch.object(Path, "read_bytes", altered(fault_vvp[0])):
        rejects(lambda: probe.verify(receipt, corpus), "artifact drift")

    with patch.object(probe, "interpret", return_value="UNDETERMINED"):
        rejects(lambda: probe.verify(receipt, corpus), "sensitivity drift")

    return {"valid": True, "checks_passed": 5,
            "scope": "evaluator-only qualification integrity"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.receipt, args.corpus), sort_keys=True))


if __name__ == "__main__":
    main()
