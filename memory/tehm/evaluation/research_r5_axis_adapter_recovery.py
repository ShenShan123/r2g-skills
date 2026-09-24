"""Verify a relocated R5 axis_adapter evidence copy without reading old artifacts.

This audits old execution identity after restoration; it is not a new EDA run,
an independent durable backup, or a portable dependency package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import research_r5_axis_adapter_q_probe as probe
from .research_r5_verdict import cocotb_verdict


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify(*, restored_case: Path, corpus: Path, archive: Path,
           archive_sha256: str) -> dict:
    restored_case = restored_case.resolve(strict=True)
    corpus = corpus.resolve(strict=True)
    archive = archive.resolve(strict=True)
    require(restored_case.is_dir() and archive.is_file(), "missing restore or archive")
    require(hashlib.sha256(archive.read_bytes()).hexdigest() == archive_sha256,
            "archive SHA256 drift")
    require(not any(path.is_symlink() for path in restored_case.rglob("*")),
            "restored case contains a symlink")
    require(not any(path.name == ".git" for path in restored_case.rglob("*")),
            "restored case contains git metadata")

    receipt_path = restored_case / "receipt-r1.json"
    receipt = json.loads(receipt_path.read_text())
    unsigned = {key: value for key, value in receipt.items() if key != "digest"}
    require(receipt["schema"] == probe.SCHEMA and
            receipt["digest"] == probe.sha(probe.canonical(unsigned)),
            "restored receipt digest or schema")
    original_case = Path(receipt["preregistration"]["path"]).parent
    require(original_case.is_absolute() and original_case.name == restored_case.name,
            "restored case identity mismatch")

    expected_files = {Path("receipt-r1.json")}

    def relocated(ref: dict) -> Path:
        old = Path(ref["path"])
        try:
            relative = old.relative_to(original_case)
        except ValueError as error:
            raise ValueError("artifact escaped original case") from error
        candidate = restored_case / relative
        require(candidate.is_file() and not candidate.is_symlink() and
                candidate.resolve(strict=True).is_relative_to(restored_case),
                f"missing or escaped restored artifact: {relative}")
        payload = candidate.read_bytes()
        require(probe.sha(payload) == ref["sha256"] and len(payload) == ref["bytes"],
                f"restored artifact drift: {relative}")
        expected_files.add(relative)
        return candidate

    prereg = relocated(receipt["preregistration"])
    require(prereg == restored_case / "preregistration.json", "preregistration path drift")
    inputs = probe.stage_inputs(restored_case, corpus)
    require(inputs["prereg"]["role"] == "EVALUATOR_ONLY_QUALIFICATION_NOT_TRAIN_OR_TRANSFER",
            "restored role drift")
    require(probe.artifact(Path(probe.__file__)) == receipt["auditor_code"] and
            probe.artifact(Path(cocotb_verdict.__code__.co_filename)) ==
            receipt["verdict_adapter_code"], "auditor code drift")

    replay = {}
    for name in ("clean", "fault"):
        row = receipt["arms"][name]
        require(row["pytest_command"] == probe.COMMAND, f"command drift: {name}")
        refs = row["artifacts"]
        paths = {label: relocated(ref) for label, ref in refs.items()}
        require(paths["staged_source"] == restored_case / name / "stage" / probe.SOURCE and
                paths["staged_test"] == restored_case / name / "stage" / probe.TEST,
                f"restored source/test path drift: {name}")
        junit = paths["cocotb_junit"]
        vvp = paths["compiled_vvp"]
        require(junit == probe.locate_junit(restored_case / name / "stage") and
                vvp == probe.locate_vvp(restored_case / name / "stage"),
                f"restored result path drift: {name}")
        historical_source = refs["staged_source"]["path"].encode()
        log = paths["pytest_log"].read_bytes()
        require(historical_source in vvp.read_bytes() and historical_source in log,
                f"historical compiled source identity drift: {name}")
        exit_code = int(paths["pytest_exit"].read_text().strip())
        verdict = cocotb_verdict(junit=junit.read_bytes(), runner_log=log,
                                 expected_test_ids=probe.TEST_IDS,
                                 runner_exit=exit_code)
        require(verdict == row["verdict"] and verdict.get("random_seed") == probe.SEED,
                f"restored verdict replay drift: {name}")
        replay[name] = verdict

    found_files = {path.relative_to(restored_case) for path in restored_case.rglob("*")
                   if path.is_file()}
    require(found_files == expected_files, "restored file inventory drift")
    sensitivity = probe.interpret(replay["clean"], replay["fault"],
                                  (restored_case / "fault/pytest.log").read_bytes())
    require(sensitivity == receipt["sensitivity"], "restored sensitivity drift")
    return {"valid": True, "restored_old_execution_identity": True,
            "restored_file_count": len(found_files),
            "clean": replay["clean"]["verdict"],
            "fault": replay["fault"]["verdict"],
            "sensitivity": sensitivity, "receipt_digest": receipt["digest"],
            "durable_backup_verified": False, "dependencies_packaged": False,
            "new_execution": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restored-case", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(restored_case=args.restored_case, corpus=args.corpus,
                            archive=args.archive,
                            archive_sha256=args.archive_sha256),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
