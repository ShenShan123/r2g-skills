"""QF-1 index of existing, bounded RTL qualification evidence (no simulation)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from tehm.assets.guard_binding import CONTRACT, locate_guard_conjunction
from .research_github_corpus import verify_github_rtl_corpus
from .research_r5_verdict import cocotb_verdict, secworks_verdict


SCHEMA = "tehm-r5-qf1-index-v3"
COCOTB_SCOPES = (
    ("axis-register", "alexforencich/verilog-axis", "rtl/axis_register.v",
     "tb/axis_register/test_axis_register.py", "test_axis_register-8-2",
     ("run_test_001", "run_test_002", "run_test_003", "run_test_004",
      "run_test_tuser_assert_001", "run_stress_test_001",
      "run_stress_test_002", "run_stress_test_003", "run_stress_test_004"),
     {"DATA_WIDTH": 8, "KEEP_ENABLE": 0, "KEEP_WIDTH": 1,
      "LAST_ENABLE": 1, "ID_ENABLE": 1, "ID_WIDTH": 8,
      "DEST_ENABLE": 1, "DEST_WIDTH": 8,
      "USER_ENABLE": 1, "USER_WIDTH": 1, "REG_TYPE": 2}),
    ("uart-rx", "alexforencich/verilog-uart", "rtl/uart_rx.v",
     "tb/uart_rx/test_uart_rx.py", "test_uart_rx",
     ("run_test_001", "run_test_002"), {"DATA_WIDTH": 8}),
)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _encoded(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def _file(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": _sha(data), "bytes": len(data)}


def _one_xml(build: Path) -> Path:
    matches = sorted(build.glob("*_results.xml"))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one cocotb JUnit: {build}")
    return matches[0]


def _git(repo: Path, *args: str) -> str:
    run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                         check=False, timeout=30)
    if run.returncode:
        raise ValueError(f"git {args[0]} failed for {repo}")
    return run.stdout.decode().strip()


def _scope(root: Path, q: Path, row: tuple) -> dict:
    name, spec, source, test, build_name, expected_ids, parameters = row
    repo = root / spec
    clean = q / f"{name}-20260924"
    probe = q / f"{name}-negative-control-20260924"
    source_clean = clean / "stage" / source
    source_probe = probe / "stage" / source
    test_clean = clean / "stage" / test
    test_probe = probe / "stage" / test
    prereg = probe / "preregistration.json"
    manifest = json.loads(prereg.read_text(encoding="utf-8"))
    original = (repo / source).read_bytes()
    if source_clean.read_bytes() != original or test_clean.read_bytes() != (repo / test).read_bytes():
        raise ValueError(f"clean staging differs from pinned upstream: {name}")
    if test_probe.read_bytes() != test_clean.read_bytes():
        raise ValueError(f"probe test differs from clean test: {name}")
    if manifest["source_git_sha"] != _git(repo, "rev-parse", "HEAD"):
        raise ValueError(f"probe repository SHA differs: {name}")
    if manifest["source_sha256"] != hashlib.sha256(original).hexdigest():
        raise ValueError(f"probe source SHA differs: {name}")
    before = manifest["single_replacement_before"].encode()
    after = manifest["single_replacement_after"].encode()
    line = manifest.get("single_replacement_line")
    if line is not None:
        lines = original.splitlines(keepends=True)
        if not isinstance(line, int) or not 1 <= line <= len(lines) or lines[line-1].rstrip(b"\r\n") != before:
            raise ValueError(f"preregistered source line mismatch: {name}")
        lines[line-1] = lines[line-1].replace(before, after, 1)
        expected_probe = b"".join(lines)
    else:
        if original.count(before) != 1:
            raise ValueError(f"preregistered source phrase not unique: {name}")
        expected_probe = original.replace(before, after, 1)
    if source_probe.read_bytes() != expected_probe:
        raise ValueError(f"probe is not the preregistered one-line change: {name}")
    if manifest["testbench_sha256"] != hashlib.sha256(test_clean.read_bytes()).hexdigest():
        raise ValueError(f"testbench SHA differs: {name}")
    subdir = Path(test).parent
    clean_xml = _one_xml(clean / "stage" / subdir / "sim_build" / build_name)
    probe_xml = _one_xml(probe / "stage" / subdir / "sim_build" / build_name)
    clean_log = clean / "pytest.log"
    probe_log = probe / "pytest.log"
    results = [cocotb_verdict(junit=xml.read_bytes(), runner_log=log.read_bytes(),
               expected_test_ids=expected_ids, runner_exit=None,
               require_recorded_exit=False)
               for xml, log in ((clean_xml, clean_log), (probe_xml, probe_log))]
    if results[0]["verdict"] != "PASS":
        raise ValueError(f"historical clean JUnit no longer passes: {name}")
    sensitivity = ("DETECTED" if results[1]["verdict"] == "FAIL" else
                   "MISSED" if results[1]["verdict"] == "PASS" else "UNDETERMINED")
    return {
        "scope_id": name + "-payload-probe-20260924", "role": "QUALIFICATION_DEV_ONLY",
        "repository": spec, "git_sha": _git(repo, "rev-parse", "HEAD"),
        "dut": Path(source).stem, "parameters": parameters,
        "parameters_complete": True,
        "test_ids": list(expected_ids), "observed_seeds": [r.get("random_seed") for r in results],
        "historical_wrapper_exit_recorded": False,
        "ordered_source_closure": [source], "source_closure_completeness": "test_wrapper_declared_single_rtl",
        "probe_class": "output_payload_inversion", "sensitivity": sensitivity,
        "qualification": ("QUALIFIED_FOR_DECLARED_SCOPE" if sensitivity == "DETECTED"
                          else "REJECTED_FOR_DECLARED_SCOPE" if sensitivity == "MISSED"
                          else "PENDING"),
        "clean_verdict": results[0], "probe_verdict": results[1],
        "artifacts": {label: _file(path) for label, path in (
            ("upstream_source", repo / source), ("upstream_test", repo / test),
            ("clean_staged_source", source_clean), ("clean_staged_test", test_clean),
            ("clean_junit", clean_xml), ("clean_runner_log", clean_log),
            ("probe_preregistration_private", prereg),
            ("probe_staged_source_private", source_probe),
            ("probe_staged_test_private", test_probe),
            ("probe_junit_private", probe_xml), ("probe_runner_log_private", probe_log),
        )},
    }


def _aes(root: Path, q: Path, qualification: dict) -> dict:
    repo = root / "secworks/aes"
    clean = q / "first-wave-r2-20260924" / "baselines" / "secworks__aes"
    probe = q / "aes-negative-control-20260924"
    prereg = probe / "preregistration.json"
    manifest = json.loads(prereg.read_text(encoding="utf-8"))
    source = repo / manifest["source_file"]
    original = source.read_bytes()
    staged = probe / "stage" / manifest["source_file"]
    before = manifest["single_replacement_before"].encode()
    after = manifest["single_replacement_after"].encode()
    if (manifest["source_git_sha"] != _git(repo, "rev-parse", "HEAD") or
            manifest["source_sha256"] != hashlib.sha256(original).hexdigest() or
            original.count(before) != 1 or staged.read_bytes() != original.replace(before, after, 1)):
        raise ValueError("AES probe source/preregistration mismatch")
    command = json.loads((clean / "command.json").read_text(encoding="utf-8"))
    source_files = command["source_sha256"]
    ordered_sources = [str(Path(arg).relative_to(repo)) for arg in command["compile"]
                       if arg.endswith((".v", ".sv"))]
    if (len(ordered_sources) != len(source_files) or
            set(ordered_sources) != set(source_files)):
        raise ValueError("AES compile command and source digest map differ")
    for rel, digest in source_files.items():
        if _sha((repo / rel).read_bytes()) != digest:
            raise ValueError(f"AES clean source drift: {rel}")

    clean_row = next(r for r in qualification["repositories"] if r["repository"] == "secworks/aes")
    clean_exit = clean_row["baseline"]["simulate_exit"]
    results = [secworks_verdict(stdout=(base / "simulate.stdout").read_bytes(),
               stderr=(base / "simulate.stderr").read_bytes(),
               simulate_exit=exit_code, expected_count=20, expected_bench="AES",
               require_recorded_exit=exit_code is not None)
               for base, exit_code in ((clean, clean_exit), (probe, None))]
    if [r["verdict"] for r in results] != ["PASS", "FAIL"]:
        raise ValueError("AES historical native outcome drift")
    artifacts = {"upstream_source": _file(source), "clean_command": _file(clean / "command.json"),
                 "probe_preregistration_private": _file(prereg),
                 "probe_staged_source_private": _file(staged)}
    for label, base in (("clean", clean), ("probe", probe)):
        for stream in ("compile.stdout", "compile.stderr", "simulate.stdout", "simulate.stderr"):
            artifacts[f"{label}_{stream}"] = _file(base / stream)
    return {
        "scope_id": "aes-result-word-probe-20260924", "role": "QUALIFICATION_DEV_ONLY",
        "repository": "secworks/aes", "git_sha": _git(repo, "rev-parse", "HEAD"),
        "dut": "aes", "parameters": {}, "parameters_complete": False,
        "test_ids": "native_20_cases_not_individual_registry", "observed_seeds": None,
        "historical_probe_exit_recorded": False,
        "ordered_source_closure": ordered_sources,
        "probe_class": "result_read_word_inversion", "sensitivity": "DETECTED",
        "qualification": "QUALIFIED_FOR_DECLARED_SCOPE",
        "clean_verdict": results[0], "probe_verdict": results[1], "artifacts": artifacts,
    }


def _legacy_scan(root: Path, qualification: dict) -> dict:
    rows = []
    for repo_row in qualification["repositories"]:
        for rel in repo_row["rtl_files"]:
            if Path(rel).suffix.lower() not in {".v", ".sv"}:
                continue
            path = root / repo_row["repository"] / rel
            source = path.read_text(encoding="utf-8")
            try:
                locate_guard_conjunction(source)
                status, reason = "MATCH", None
            except (ValueError, NotImplementedError) as exc:
                status, reason = "NO_MATCH_OR_UNSUPPORTED", str(exc)
            rows.append({"repository": repo_row["repository"], "path": rel,
                         "sha256": _sha(source.encode()), "status": status, "reason": reason})
    return {"contract": CONTRACT, "files_scanned": len(rows),
            "matches": sum(row["status"] == "MATCH" for row in rows), "rows": rows,
            "binding_recall": "not_established_without_independent_structural_labels"}


def build_qf1(*, corpus_root: str | Path, output: str | Path) -> dict:
    root = Path(corpus_root).resolve(strict=True)
    out = Path(output).resolve(strict=False)
    if out.exists():
        raise ValueError(f"QF-1 output already exists: {out}")
    q = root / "_qualification"
    qualification_path = q / "first-wave-r2-20260924"
    replay = verify_github_rtl_corpus(qualification_path)
    if not replay["valid"] or replay["repository_count"] != 10:
        raise ValueError(f"source qualification replay failed: {replay}")
    qualification = json.loads((qualification_path / "qualification.json").read_text(encoding="utf-8"))
    scopes = [_scope(root, q, row) for row in COCOTB_SCOPES]
    scopes.append(_aes(root, q, qualification))
    scan = _legacy_scan(root, qualification)
    payload = {
        "schema": SCHEMA, "corpus_root": str(root),
        "source_qualification": _file(qualification_path / "qualification.json"),
        "source_qualification_digest": replay["qualification_digest"],
        "implementation_locks": {
            "qf1_builder": _file(Path(__file__)),
            "verdict_adapter": _file(Path(cocotb_verdict.__code__.co_filename)),
            "legacy_binder": _file(Path(locate_guard_conjunction.__code__.co_filename))},
        "repository_locks": [{key: row[key] for key in
                              ("repository", "remote_url", "git_sha", "source_group",
                               "clean_before", "clean_after", "license_status", "license_files")}
                             for row in qualification["repositories"]],
        "scopes": scopes, "legacy_binder_scan": scan,
        "limits": ["qualification probes are not TEHM repairs or transfer",
                   "cocotb wrapper exits and AES probe exit were not independently captured",
                   "clean and fault cocotb random seeds differ",
                   "LFS and full transitive dependency lock pending",
                   "cross-owner source independence not yet established"],
        "agent_visibility": "evaluator_only_not_mounted_into_agent_or_binder",
    }
    payload["digest"] = _sha(_encoded(payload))
    out.mkdir(parents=True)
    (out / "index.json").write_bytes(_encoded(payload))
    return {"valid": True, "output": str(out), "digest": payload["digest"],
            "repository_count": len(payload["repository_locks"]),
            "scope_count": len(scopes), "legacy_files": scan["files_scanned"],
            "legacy_matches": scan["matches"]}


def verify_qf1(index: str | Path) -> dict:
    path = Path(index).resolve(strict=True)
    payload = json.loads((path / "index.json").read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "digest"}
    errors = []
    if payload.get("schema") != SCHEMA or payload.get("digest") != _sha(_encoded(body)):
        errors.append("index_digest_or_schema")
    for entry in [payload["source_qualification"],
                  *payload["implementation_locks"].values(),
                  *(artifact for scope in payload["scopes"] for artifact in scope["artifacts"].values())]:
        try:
            data = Path(entry["path"]).read_bytes()
            if len(data) != entry["bytes"] or _sha(data) != entry["sha256"]:
                errors.append("artifact_drift:" + entry["path"])
        except OSError:
            errors.append("artifact_missing:" + entry["path"])
    for lock in payload["repository_locks"]:
        repo = Path(payload["corpus_root"]) / lock["repository"]
        try:
            if (_git(repo, "rev-parse", "HEAD") != lock["git_sha"] or
                    _git(repo, "config", "--get", "remote.origin.url") != lock["remote_url"] or
                    _git(repo, "status", "--porcelain=v1")):
                errors.append("clone_drift:" + lock["repository"])
        except ValueError:
            errors.append("clone_unreadable:" + lock["repository"])
    return {"valid": not errors, "errors": errors, "digest": payload.get("digest"),
            "scope_count": len(payload["scopes"]),
            "legacy_files": payload["legacy_binder_scan"]["files_scanned"]}
