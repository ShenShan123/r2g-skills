"""Read-only positive and adversarial checks for the R5 DEV lineage screen."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from tehm.evaluation import research_r5_lineage_history_audit as audit_module


def rejected(operation, expected: str) -> None:
    try:
        operation()
    except ValueError as error:
        if expected not in str(error):
            raise AssertionError(f"wrong rejection: {error}") from error
    else:
        raise AssertionError(f"accepted invalid input: {expected}")


def run(history_dir: Path, corpus_root: Path) -> dict:
    result = audit_module.audit(history_dir, corpus_root)
    assert result["valid"] is True
    assert result["independent_lineages_verified"] is False
    assert len(result["cases"]) == 3

    original_read = Path.read_bytes
    metadata = history_dir / "verilog_axis_repo_metadata.json"

    def corrupt_metadata(path: Path) -> bytes:
        payload = original_read(path)
        return payload + b"x" if path == metadata else payload

    with patch.object(Path, "read_bytes", corrupt_metadata):
        rejected(lambda: audit_module.audit(history_dir, corpus_root), "SHA256 mismatch")

    origin = history_dir / "zipcpu_skidbuffer_origin.v"

    def corrupt_origin(path: Path) -> bytes:
        payload = original_read(path)
        return payload + b"x" if path == origin else payload

    with patch.object(Path, "read_bytes", corrupt_origin):
        rejected(lambda: audit_module.audit(history_dir, corpus_root), "SHA256 mismatch")

    with patch.object(audit_module, "code_lines", return_value=["x" * 20] * 8):
        rejected(lambda: audit_module.overlap_screen(corpus_root), "overlap screen changed")

    original_git = audit_module.git

    def wrong_head(repo: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "0" * 40
        return original_git(repo, *args)

    with patch.object(audit_module, "git", wrong_head):
        rejected(lambda: audit_module.audit(history_dir, corpus_root), "HEAD mismatch")

    return {"valid": True, "checks_passed": 5, "scope": "DEV-only; no lineage authority"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-dir", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.history_dir, args.corpus_root), sort_keys=True))


if __name__ == "__main__":
    main()
