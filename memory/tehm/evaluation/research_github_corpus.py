"""Bounded qualification of a pinned, locally cloned GitHub RTL corpus.

This is benchmark-substrate admission, not a TEHM repair experiment.  Native
baselines are run only for reviewed, explicit Icarus recipes; no Makefile,
project Python, network command, or mutation is executed.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


SCHEMA = "tehm-github-rtl-corpus-qualification-v1"
REPOSITORIES = (
    "alexforencich/verilog-uart",
    "alexforencich/verilog-axis",
    "alexforencich/verilog-i2c",
    "secworks/aes",
    "secworks/sha256",
    "secworks/chacha",
    "YosysHQ/picorv32",
    "freecores/i2c",
    "olofk/serv",
    "ultraembedded/riscv",
)
RECIPES = {
    "secworks/aes": (
        "tb_aes", "src/tb/tb_aes.v",
        ("aes.v", "aes_core.v", "aes_key_mem.v", "aes_sbox.v",
         "aes_inv_sbox.v", "aes_encipher_block.v", "aes_decipher_block.v"),
    ),
    "secworks/sha256": (
        "tb_sha256", "src/tb/tb_sha256.v",
        ("sha256.v", "sha256_core.v", "sha256_k_constants.v", "sha256_w_mem.v"),
    ),
    "secworks/chacha": (
        "tb_chacha", "src/tb/tb_chacha.v",
        ("chacha.v", "chacha_core.v", "chacha_qr.v"),
    ),
}
SUMMARY = re.compile(r"\*\*\* All\s+(\d+) test cases completed successfully\.?")


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _run(argv: list[str], *, cwd: Path, timeout: int = 20) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout,
                          check=False)

def _tool_version(argv: list[str], root: Path) -> dict:
    process = _run(argv, cwd=root, timeout=10)
    output = process.stdout + process.stderr
    lines = output.decode("utf-8", errors="replace").splitlines()
    return {"command": argv, "exit": process.returncode,
            "first_line": next((line for line in lines if line.strip()), ""),
            "output_sha256": _digest(output)}



def _git(repo: Path, *args: str) -> str:
    process = _run(["git", "-C", str(repo), *args], cwd=repo)
    if process.returncode:
        raise ValueError(f"git {args[0]} failed for {repo}")
    return process.stdout.decode("utf-8", errors="strict").strip()


def _tracked(repo: Path) -> list[str]:
    process = _run(["git", "-C", str(repo), "ls-files", "-z"], cwd=repo)
    if process.returncode:
        raise ValueError(f"cannot list tracked files: {repo}")
    paths = [path for path in process.stdout.decode("utf-8").split("\0") if path]
    for path in paths:
        candidate = repo / path
        if candidate.is_symlink():
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(repo.resolve()):
                raise ValueError(f"tracked symlink escapes repository: {candidate}")
        elif not candidate.is_file():
            raise ValueError(f"tracked file is missing: {candidate}")
    return sorted(paths)


def _baseline(repo: Path, spec: str, work: Path) -> dict:
    top, bench, rtl_names = RECIPES[spec]
    rtl = [repo / "src/rtl" / name for name in rtl_names]
    source = [repo / bench, *rtl]
    if any(not path.is_file() for path in source):
        return {"status": "BLOCKED_MISSING_SOURCE"}
    work.mkdir(parents=True, exist_ok=False)
    output = work / "baseline.vvp"
    compile_args = ["iverilog", "-Wall", "-s", top, "-o", str(output),
                    *(str(path) for path in source)]
    (work / "command.json").write_bytes(_json_bytes({
        "compile": compile_args, "simulate": ["vvp", "-N", str(output)],
        "source_sha256": {str(path.relative_to(repo)): _digest(path.read_bytes())
                          for path in source},
    }))
    try:
        compiled = _run(compile_args, cwd=work, timeout=60)
        (work / "compile.stdout").write_bytes(compiled.stdout)
        (work / "compile.stderr").write_bytes(compiled.stderr)
        if compiled.returncode:
            return {"status": "COMPILE_FAIL", "compile_exit": compiled.returncode}
        executed = _run(["vvp", "-N", str(output)], cwd=work, timeout=120)
        (work / "simulate.stdout").write_bytes(executed.stdout)
        (work / "simulate.stderr").write_bytes(executed.stderr)
    except subprocess.TimeoutExpired as exc:
        return {"status": "TIMEOUT", "phase": "compile" if not output.exists() else "simulate",
                "seconds": exc.timeout}
    stdout = executed.stdout.decode("utf-8", errors="replace")
    summaries = SUMMARY.findall(stdout)
    error_lines = [line for line in stdout.splitlines()
                   if re.search(r"\b(?:ERROR|FAIL|FATAL)\b", line, re.IGNORECASE)]
    passed = (executed.returncode == 0 and len(summaries) == 1
              and int(summaries[0]) > 0 and not error_lines)
    return {
        "status": "CLEAN_PASS" if passed else "ORACLE_FAIL_OR_UNKNOWN",
        "compile_exit": compiled.returncode,
        "simulate_exit": executed.returncode,
        "summary_count": len(summaries),
        "test_case_count": int(summaries[0]) if len(summaries) == 1 else None,
        "error_line_count": len(error_lines),
        "stdout_sha256": _digest(executed.stdout),
        "stderr_sha256": _digest(executed.stderr),
        "compile_stdout_sha256": _digest(compiled.stdout),
        "compile_stderr_sha256": _digest(compiled.stderr),
        "oracle": "native_secworks_summary_plus_exit_v1",
    }


def qualify_github_rtl_corpus(*, corpus_root: str | Path, output: str | Path,
                              run_native_baselines: bool = False) -> dict:
    root = Path(corpus_root).resolve(strict=True)
    target = Path(output).resolve(strict=False)
    if target.exists():
        raise ValueError(f"refusing to overwrite qualification: {target}")
    if not root.is_dir():
        raise ValueError("corpus root is not a directory")
    staging = target.with_name(target.name + ".staging")
    if staging.exists():
        raise ValueError(f"qualification staging exists: {staging}")
    if any(target.is_relative_to(root / spec) for spec in REPOSITORIES):
        raise ValueError("qualification output cannot be inside a cloned repository")
    staging.mkdir(parents=True)
    rows = []
    for spec in REPOSITORIES:
        repo = root / spec
        if not repo.is_dir() or not (repo / ".git").is_dir():
            rows.append({"repository": spec, "status": "MISSING_CLONE"})
            continue
        before = _git(repo, "status", "--porcelain=v1")
        tracked = _tracked(repo)
        licenses = [path for path in tracked
                    if Path(path).name.upper().startswith(("LICENSE", "COPYING"))
                    and len(Path(path).parts) == 1]
        rtl = [path for path in tracked if Path(path).suffix.lower() in
               {".v", ".sv", ".vhd", ".vhdl"} and not
               ({"tb", "bench", "test", "tests", "sim"} & set(Path(path).parts))]
        benches = [path for path in tracked if
                   ({"tb", "bench", "test", "tests"} & set(Path(path).parts)
                    or Path(path).name.lower().startswith(("tb_", "testbench")))]
        row = {
            "repository": spec,
            "source_group": spec.split("/", 1)[0].lower(),
            "git_sha": _git(repo, "rev-parse", "HEAD"),
            "remote_url": _git(repo, "config", "--get", "remote.origin.url"),
            "clean_before": not before,
            "license_files": {path: _digest((repo / path).read_bytes())
                              for path in licenses},
            "license_status": "FILE_PRESENT_UNREVIEWED" if licenses else "REVIEW_REQUIRED",
            "rtl_file_count": len(rtl),
            "testbench_file_count": len(benches),
            "rtl_files": rtl,
            "testbench_files": benches,
            "rtl_roots": sorted({Path(path).parts[0] for path in rtl}),
            "testbench_roots": sorted({Path(path).parts[0] for path in benches}),
            "framework_hints": [path for path in tracked if Path(path).name in
                                {"tox.ini", "Makefile"} or path.endswith(".core")],
        }
        if spec in RECIPES and run_native_baselines and not before:
            row["baseline"] = _baseline(repo, spec, staging / "baselines" /
                                        spec.replace("/", "__"))
        else:
            reason = ("DIRTY_CLONE" if before else "NOT_RUN" if spec in RECIPES
                      else "NO_REVIEWED_NATIVE_RECIPE")
            row["baseline"] = {"status": reason}
        row["clean_after"] = not _git(repo, "status", "--porcelain=v1")
        row["status"] = ("CLEAN_BASELINE_VERIFIED" if
                         row["baseline"]["status"] == "CLEAN_PASS" and
                         row["clean_before"] and row["clean_after"] and licenses and rtl
                         else "QUALIFICATION_PENDING")
        rows.append(row)
    payload = {
        "schema": SCHEMA, "corpus_root": str(root),
        "native_baselines_requested": run_native_baselines,
        "tool_versions": {name: _tool_version(argv, root) for name, argv in (
            ("git", ["git", "--version"]),
            ("iverilog", ["iverilog", "-V"]),
            ("vvp", ["vvp", "-V"]),
        )},
        "repositories": rows,
        "qualification_scope": "clean upstream substrate only; no mutation, TEHM, transfer or delta-memory claim",
    }
    payload["digest"] = _digest(_json_bytes(payload))
    (staging / "qualification.json").write_bytes(_json_bytes(payload))
    staging.rename(target)
    return {
        "valid": all(row["status"] != "MISSING_CLONE" and row.get("clean_after", False)
                     for row in rows),
        "repository_count": len(rows),
        "clean_baseline_count": sum(row["status"] == "CLEAN_BASELINE_VERIFIED"
                                    for row in rows),
        "qualification_digest": payload["digest"],
        "output": str(target),
    }

def verify_github_rtl_corpus(qualification: str | Path) -> dict:
    """Replay the frozen receipt against raw logs and current clean clones."""
    output = Path(qualification).resolve(strict=True)
    payload = json.loads((output / "qualification.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    body = {key: value for key, value in payload.items() if key != "digest"}
    if payload.get("schema") != SCHEMA or payload.get("digest") != _digest(_json_bytes(body)):
        errors.append("qualification_digest_or_schema")
    rows = payload.get("repositories")
    if not isinstance(rows, list) or [row.get("repository") for row in rows] != list(REPOSITORIES):
        return {"valid": False, "errors": errors + ["repository_denominator"]}
    root = Path(payload["corpus_root"]).resolve(strict=True)
    for row in rows:
        spec = row["repository"]
        repo = root / spec
        try:
            if _git(repo, "rev-parse", "HEAD") != row["git_sha"]:
                errors.append(f"{spec}:git_sha")
            if _git(repo, "config", "--get", "remote.origin.url") != row["remote_url"]:
                errors.append(f"{spec}:remote")
            if _git(repo, "status", "--porcelain=v1"):
                errors.append(f"{spec}:dirty")
            for path, digest in row["license_files"].items():
                if _digest((repo / path).read_bytes()) != digest:
                    errors.append(f"{spec}:license:{path}")
        except (OSError, ValueError, KeyError):
            errors.append(f"{spec}:clone_unreadable")
        baseline = row.get("baseline") or {}
        if baseline.get("status") != "CLEAN_PASS":
            continue
        work = output / "baselines" / spec.replace("/", "__")
        for name, field in (("compile.stdout", "compile_stdout_sha256"),
                            ("compile.stderr", "compile_stderr_sha256"),
                            ("simulate.stdout", "stdout_sha256"),
                            ("simulate.stderr", "stderr_sha256")):
            try:
                if _digest((work / name).read_bytes()) != baseline[field]:
                    errors.append(f"{spec}:{name}_digest")
            except (OSError, KeyError):
                errors.append(f"{spec}:{name}_missing")
        try:
            stdout = (work / "simulate.stdout").read_text(encoding="utf-8", errors="replace")
            found = SUMMARY.findall(stdout)
            if (len(found) != 1 or int(found[0]) != baseline["test_case_count"]
                    or re.search(r"\b(?:ERROR|FAIL|FATAL)\b", stdout, re.IGNORECASE)):
                errors.append(f"{spec}:oracle_summary")
        except (OSError, KeyError):
            errors.append(f"{spec}:oracle_unreadable")
    return {"valid": not errors, "errors": errors,
            "repository_count": len(rows),
            "clean_baseline_count": sum(row.get("baseline", {}).get("status") == "CLEAN_PASS"
                                        for row in rows),
            "qualification_digest": payload.get("digest")}
