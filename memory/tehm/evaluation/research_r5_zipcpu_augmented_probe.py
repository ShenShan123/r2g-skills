"""R5 DEV-only augmented transaction oracle for ZipCPU skidbuffer payload.

The private testbench is an explicit external input under _qualification, not
an upstream/native test and not part of any agent-visible task.  No TRAIN or
held-out target authority follows from this probe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from . import research_r5_zipcpu_skid_dev_probe as native


SCHEMA = "tehm-r5-zipcpu-augmented-payload-dev-v1"
REPO = native.REPO
SOURCE = native.SOURCE
TOP = "tb_skidbuffer_payload"
SCENARIOS = {"direct": 0, "backpressure": 1}
IV = Path("/usr/bin/iverilog")
VVP = Path("/usr/bin/vvp")
_PASS = re.compile(r"^TEHM_AUGMENTED_PASS scenario=([01]) sent=5 received=5$", re.M)
_FAIL = re.compile(r"^TEHM_AUGMENTED_FAIL payload_mismatch scenario=1 ", re.M)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def _ref(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": _sha(data),
            "bytes": len(data)}


def _tools() -> dict:
    result = {}
    for name, path in (("iverilog", IV), ("vvp", VVP)):
        resolved = path.resolve(strict=True)
        version = subprocess.run([str(resolved), "-V" if name == "iverilog" else "-V"],
                                 capture_output=True, check=True, timeout=15)
        result[name] = {"path": str(resolved), "sha256": _sha(resolved.read_bytes()),
                        "version_sha256": _sha(version.stdout + version.stderr)}
    return result


def _cmd(arm: Path, *, scenario: int) -> tuple[list[str], list[str]]:
    stage = arm / "stage"
    image = arm / "build" / "sim.vvp"
    compile_cmd = [str(IV), "-g2012", "-s", TOP,
                   f"-P{TOP}.BACKPRESSURE={scenario}", "-o", str(image),
                   str(stage / SOURCE), str(stage / "tb" / "tb_skidbuffer_payload.sv")]
    return compile_cmd, [str(VVP), str(image)]


def _classify(arm: Path, scenario: int) -> dict:
    compile_rc = int((arm / "compile.exit").read_text(encoding="utf-8"))
    run_rc = int((arm / "run.exit").read_text(encoding="utf-8"))
    compile_log = (arm / "compile.log").read_text(encoding="utf-8", errors="replace")
    run_log = (arm / "run.log").read_text(encoding="utf-8", errors="replace")
    pass_match = list(_PASS.finditer(run_log))
    fail_match = list(_FAIL.finditer(run_log))
    if compile_rc != 0 or "error:" in compile_log.lower():
        return {"verdict": "UNDETERMINED", "reason": "compile_failure"}
    if (run_rc == 0 and len(pass_match) == 1 and
            int(pass_match[0].group(1)) == scenario and not fail_match and
            "TEHM_AUGMENTED_UNKNOWN" not in run_log):
        return {"verdict": "PASS", "reason": "scoreboard_all_five_beats_equal"}
    if (run_rc != 0 and len(fail_match) == 1 and scenario == 1 and
            not pass_match and "FATAL:" in run_log):
        return {"verdict": "FAIL", "reason": "scoreboard_payload_mismatch"}
    return {"verdict": "UNDETERMINED", "reason": "run_exit_or_summary_mismatch"}


def run(*, corpus: Path, private_tb: Path, work: Path) -> dict:
    corpus = corpus.resolve(strict=True)
    private_tb = private_tb.resolve(strict=True)
    work = work.resolve(strict=False)
    if (work.exists() or work.parent != corpus / "_qualification" or
            corpus / "_qualification" not in private_tb.parents):
        raise ValueError("DEV work/input must be isolated _qualification children")
    repo = corpus / REPO
    if native._git(repo, "status", "--porcelain=v1"):
        raise ValueError("upstream ZipCPU checkout is dirty")
    source = (repo / SOURCE).read_bytes()
    fault = native._fault(source)
    tb = private_tb.read_bytes()
    if not tb or b"module tb_skidbuffer_payload;" not in tb:
        raise ValueError("private augmented testbench is missing its frozen top")
    script = Path(__file__).resolve(strict=True)
    prereg = {"schema": SCHEMA, "role": "DEV_RESEARCH_AUGMENTED_NOT_NATIVE_OR_TRAIN",
              "source_repository": REPO, "source_git_sha": native._git(repo, "rev-parse", "HEAD"),
              "source_file": SOURCE, "source_sha256": _sha(source),
              "private_testbench": str(private_tb), "private_testbench_sha256": _sha(tb),
              "producer_code_sha256": _sha(script.read_bytes()),
              "native_miss_reference": "r5-dev-zipcpu-skid-formal-20260924-r1",
              "public_parameters": {"DW": 8, "OPT_OUTREG": 1,
                                    "OPT_LOWPOWER": 0, "OPT_PASSTHROUGH": 0},
              "top": TOP, "scenarios": SCENARIOS,
              "random_seed": "not_used_deterministic_schedule",
              "fault_line": native.FAULT_LINE, "fault_before": native.OLD.decode(),
              "fault_after": native.NEW.decode(),
              "target_obligation": "accepted_payload_beats_reappear_in_order_after_backpressure",
              "preservation_obligation": "direct_no_backpressure_path_still_matches_payload",
              "expected_clean": {"direct": "PASS", "backpressure": "PASS"},
              "hypothesized_fault": {"direct": "PASS", "backpressure": "FAIL"},
              "timeout_seconds_compile": 30, "timeout_seconds_run": 20,
              "upstream_mutation_allowed": False, "agent_visible": False,
              "production_authority": False, "tools": _tools()}
    work.mkdir()
    (work / "preregistration.json").write_bytes(_json(prereg))
    for name, data in (("clean", source), ("fault", fault)):
        for label, scenario in SCENARIOS.items():
            arm = work / name / label
            stage = arm / "stage"
            (stage / "rtl").mkdir(parents=True)
            (stage / "tb").mkdir(parents=True)
            (arm / "build").mkdir()
            (stage / SOURCE).write_bytes(data)
            (stage / "tb" / "tb_skidbuffer_payload.sv").write_bytes(tb)
            compile_cmd, run_cmd = _cmd(arm, scenario=scenario)
            (arm / "commands.json").write_bytes(_json({
                "compile": compile_cmd, "run": run_cmd}))
            rc, output = native._run(compile_cmd, cwd=stage, timeout=30)
            (arm / "compile.log").write_bytes(output)
            (arm / "compile.exit").write_text(str(rc) + "\n", encoding="utf-8")
            if rc == 0:
                rc, output = native._run(run_cmd, cwd=stage, timeout=20)
            else:
                rc, output = 125, b"run_skipped_due_to_compile_failure\n"
            (arm / "run.log").write_bytes(output)
            (arm / "run.exit").write_text(str(rc) + "\n", encoding="utf-8")
    return verify(work)


def verify(work: Path) -> dict:
    root = work.resolve(strict=True)
    prereg_path = root / "preregistration.json"
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    errors = []
    corpus = root.parent.parent
    repo = corpus / REPO
    source = (repo / SOURCE).read_bytes()
    tb_path = Path(prereg["private_testbench"]).resolve(strict=True)
    tb = tb_path.read_bytes()
    if (prereg.get("schema") != SCHEMA or
            prereg.get("role") != "DEV_RESEARCH_AUGMENTED_NOT_NATIVE_OR_TRAIN" or
            prereg.get("source_git_sha") != native._git(repo, "rev-parse", "HEAD") or
            native._git(repo, "status", "--porcelain=v1") or
            prereg.get("source_sha256") != _sha(source) or
            prereg.get("private_testbench_sha256") != _sha(tb) or
            prereg.get("producer_code_sha256") != _sha(Path(__file__).read_bytes()) or
            prereg.get("top") != TOP or prereg.get("scenarios") != SCENARIOS or
            prereg.get("fault_line") != native.FAULT_LINE or
            prereg.get("fault_before") != native.OLD.decode() or
            prereg.get("fault_after") != native.NEW.decode() or
            prereg.get("tools") != _tools()):
        errors.append("prereg_source_test_code_or_tools_drift")
    arms = {}
    for name, data in (("clean", source), ("fault", native._fault(source))):
        for label, scenario in SCENARIOS.items():
            arm = root / name / label
            stage = arm / "stage"
            try:
                if ((stage / SOURCE).read_bytes() != data or
                        (stage / "tb" / "tb_skidbuffer_payload.sv").read_bytes() != tb or
                        any(path.is_symlink() or path.name == ".git"
                            for path in stage.rglob("*"))):
                    errors.append(f"{name}/{label}:staged_input_drift")
                compile_cmd, run_cmd = _cmd(arm, scenario=scenario)
                if json.loads((arm / "commands.json").read_text(encoding="utf-8")) != {
                        "compile": compile_cmd, "run": run_cmd}:
                    errors.append(f"{name}/{label}:command_drift")
                result = _classify(arm, scenario)
                image = arm / "build" / "sim.vvp"
                if not image.is_file() or str(stage / SOURCE).encode() not in image.read_bytes():
                    errors.append(f"{name}/{label}:compiled_source_identity_missing")
                result["artifacts"] = {key: _ref(path) for key, path in (
                    ("staged_source", stage / SOURCE),
                    ("staged_test", stage / "tb" / "tb_skidbuffer_payload.sv"),
                    ("compiled_image", image),
                    ("compile_log", arm / "compile.log"),
                    ("compile_exit", arm / "compile.exit"),
                    ("run_log", arm / "run.log"),
                    ("run_exit", arm / "run.exit"))}
                arms[f"{name}/{label}"] = result
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{name}/{label}:artifact_unavailable:{exc}")
    clean = all(arms.get(f"clean/{label}", {}).get("verdict") == "PASS"
                for label in SCENARIOS)
    preservation = arms.get("fault/direct", {}).get("verdict") == "PASS"
    target = arms.get("fault/backpressure", {}).get("verdict")
    if clean and preservation and target == "FAIL" and not errors:
        qualification = "DETECTED"
    elif clean and preservation and target == "PASS" and not errors:
        qualification = "MISSED"
    else:
        qualification = "UNDETERMINED"
    result = {"schema": SCHEMA, "valid": not errors,
              "qualification": qualification, "clean_all_pass": clean,
              "preservation_pass": preservation,
              "target_verdict": target, "errors": errors,
              "preregistration_sha256": _sha(prereg_path.read_bytes()),
              "arms": arms}
    result["audit_digest"] = _sha(_json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--corpus", type=Path, required=True)
    run_p.add_argument("--private-tb", type=Path, required=True)
    run_p.add_argument("--work", type=Path, required=True)
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    result = (run(corpus=args.corpus, private_tb=args.private_tb, work=args.work)
              if args.command == "run" else verify(args.work))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid") and result.get("qualification") == "DETECTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
