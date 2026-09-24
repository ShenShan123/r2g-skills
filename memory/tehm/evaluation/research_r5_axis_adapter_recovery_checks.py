"""Adversarial checks that R5 restored evidence is read from the new path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from tehm.evaluation import research_r5_axis_adapter_recovery as recovery


def rejected(operation, reason: str) -> None:
    try:
        operation()
    except ValueError as error:
        if reason not in str(error):
            raise AssertionError(f"wrong rejection: {error}") from error
    else:
        raise AssertionError(f"tampering accepted: {reason}")


def run(restored_case: Path, corpus: Path, archive: Path, archive_sha256: str) -> dict:
    def check() -> dict:
        return recovery.verify(restored_case=restored_case, corpus=corpus,
                               archive=archive, archive_sha256=archive_sha256)

    result = check()
    assert result["valid"] and result["restored_old_execution_identity"]
    assert result["new_execution"] is False

    original_case = Path(json.loads((restored_case / "receipt-r1.json").read_text())
                         ["preregistration"]["path"]).parent
    original_read = Path.read_bytes
    original_read_text = Path.read_text

    def reject_original_reads(path: Path) -> bytes:
        if path.is_relative_to(original_case):
            raise AssertionError(f"read original evidence instead of restore: {path}")
        return original_read(path)

    def reject_original_text(path: Path, *args, **kwargs) -> str:
        if path.is_relative_to(original_case):
            raise AssertionError(f"read original evidence instead of restore: {path}")
        return original_read_text(path, *args, **kwargs)

    with (patch.object(Path, "read_bytes", reject_original_reads),
          patch.object(Path, "read_text", reject_original_text)):
        assert check()["valid"]

    def altered(target: Path):
        def read(path: Path) -> bytes:
            payload = original_read(path)
            return payload + b"x" if path == target else payload
        return read

    with patch.object(Path, "read_bytes", altered(archive)):
        rejected(check, "archive SHA256 drift")

    fault_source = restored_case / "fault/stage/rtl/axis_adapter.v"
    with patch.object(Path, "read_bytes", altered(fault_source)):
        rejected(check, "staged input drift")

    junit_dir = restored_case / "fault/stage/tb/axis_adapter/sim_build/test_axis_register-8-16"
    matches = list(junit_dir.glob("*_results.xml"))
    assert len(matches) == 1
    with patch.object(Path, "read_bytes", altered(matches[0])):
        rejected(check, "restored artifact drift")

    return {"valid": True, "checks_passed": 5,
            "scope": "relocated old-evidence identity only"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restored-case", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.restored_case, args.corpus, args.archive,
                         args.archive_sha256), sort_keys=True))


if __name__ == "__main__":
    main()
