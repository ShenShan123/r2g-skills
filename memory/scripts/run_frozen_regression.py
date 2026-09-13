"""Run the complete memory test suite with a frozen source/JUnit provenance.

The output must be new. Terminal failures are retained, never overwritten.
This is engineering verification, not hardware or provider evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def corpus(memory):
    memory = Path(memory).resolve()
    paths = subprocess.run(["rg", "--files", "-g", "*.py", str(memory)],
        check=True, capture_output=True, text=True).stdout.splitlines()
    if not paths:
        raise ValueError("Python source corpus is empty")
    entries = [{"relative_path": Path(path).relative_to(memory).as_posix(),
        "sha256": "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()} for path in sorted(paths)]
    return {"files": entries, "file_count": len(entries), "corpus_digest": digest(entries)}


def junit_totals(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        raise ValueError("JUnit has no test suites")
    return {name: sum(int(s.get(name, "0")) for s in suites)
        for name in ("tests", "failures", "errors", "skipped")}


def terminal_result(frozen, current, totals, exit_code, minimum_tests):
    unchanged = current == frozen
    passed = (exit_code == 0 and unchanged and totals["tests"] >= minimum_tests
        and all(totals[name] == 0 for name in ("failures", "errors", "skipped")))
    return {"status": "PASS" if passed else "FAIL", "exit_code": exit_code,
        "source_corpus_unchanged": unchanged, "junit_totals": totals,
        "minimum_tests": minimum_tests}


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


def run(output, minimum_tests):
    if minimum_tests < 1:
        raise ValueError("minimum_tests must be positive")
    memory = Path(__file__).resolve().parents[1]
    output = Path(output).resolve()
    # Prevent generated artifacts from changing the corpus being verified.
    if output.is_relative_to(memory):
        raise ValueError("regression output must be outside memory source tree")
    output.mkdir(parents=True, exist_ok=False)
    frozen = corpus(memory)
    junit = output / "junit.xml"
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        str(memory / "tests"), "--junitxml=" + str(junit)]
    environment = dict(os.environ)
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join((str(memory), str(memory / "tests"))))
    inputs = {"version": "memory-full-regression-input-freeze-v1", "source_corpus": frozen,
        "python_executable": sys.executable, "python_version": sys.version,
        "python_binary_sha256": "sha256:" + hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        "command": command, "environment_overrides": {k: environment[k] for k in ("PYTHONDONTWRITEBYTECODE", "PYTHONPATH")},
        "minimum_tests": minimum_tests, "scope": "complete memory/tests; engineering only"}
    inputs["report_digest"] = digest(inputs)
    write_new(output / "input-freeze.json", inputs)
    print(json.dumps({"status": "STARTED", "source_files": frozen["file_count"],
        "input_freeze_digest": inputs["report_digest"]}), flush=True)
    result = subprocess.run(command, cwd=memory.parent, env=environment)
    try:
        totals = junit_totals(junit)
        final = terminal_result(frozen, corpus(memory), totals, result.returncode, minimum_tests)
    except (ValueError, OSError, ET.ParseError) as exc:
        final = {"status": "FAIL", "exit_code": result.returncode, "artifact_error": str(exc)}
    final.update(version="memory-full-regression-terminal-v1", input_freeze_digest=inputs["report_digest"],
        junit_path=str(junit), engineering_verification_only=True)
    if junit.is_file():
        final["junit_sha256"] = "sha256:" + hashlib.sha256(junit.read_bytes()).hexdigest()
    final["report_digest"] = digest(final)
    write_new(output / "terminal-report.json", final)
    print(json.dumps(final), flush=True)
    return 0 if final["status"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--minimum-tests", type=int, default=1600)
    args = parser.parse_args(argv)
    return run(args.output, args.minimum_tests)


if __name__ == "__main__":
    raise SystemExit(main())
