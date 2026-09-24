"""Replay a research-only skid binder candidate against staged native evidence."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .research_r5_qualification import COCOTB_SCOPES
from .research_r5_skid_binding import (
    apply_bound_skid_payload, bind_skid_payload,
)
from .research_r5_verdict import cocotb_verdict


SCHEMA = "tehm-r5-skid-dev-candidate-audit-v1"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def _ref(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": _sha(data), "bytes": len(data)}


def _assess(work: Path) -> dict:
    work = work.resolve(strict=True)
    stage_receipt = work / "stage-receipt.json"
    staged = json.loads(stage_receipt.read_text(encoding="utf-8"))
    if (staged.get("schema") != "r5-dev-binder-candidate-stage-v1" or
            staged.get("role") != "DEV_SMOKE_NOT_MEMORY_TRANSFER"):
        raise ValueError("candidate stage role/schema drift")
    agent_root = work / "agent-inputs"
    visible = agent_root / "rtl/axis_register.v"
    inventory = sorted(p.relative_to(agent_root).as_posix() for p in agent_root.rglob("*") if p.is_file())
    if inventory != ["rtl/axis_register.v"] or any(p.is_symlink() for p in agent_root.rglob("*")):
        raise ValueError("agent stage contains private or linked files")
    candidate = work / "evaluator/stage/rtl/axis_register.v"
    test = work / "evaluator/stage/tb/axis_register/test_axis_register.py"
    source = visible.read_text(encoding="utf-8")
    asset = staged["asset"]
    context = staged["public_context"]
    binding = bind_skid_payload(asset, source, context)
    if binding.get("status") != "BOUND" or binding != staged["binding"]:
        raise ValueError("source-only binding did not replay")
    edited, edit = apply_bound_skid_payload(asset, source, context, binding)
    if edited.encode() != candidate.read_bytes() or edit != staged["action"]:
        raise ValueError("candidate does not replay the bound action")
    if (_sha(visible.read_bytes()) != staged["agent_visible_source_sha256"] or
            _sha(candidate.read_bytes()) != staged["candidate_source_sha256"] or
            _sha(test.read_bytes()) != staged["test_sha256"]):
        raise ValueError("staged file digest drift")
    if (test.read_bytes() != (work.parent.parent.parent /
            "alexforencich/verilog-axis/tb/axis_register/test_axis_register.py").read_bytes()):
        raise ValueError("testbench differs from pinned upstream")
    build = work / "evaluator/stage/tb/axis_register/sim_build/test_axis_register-8-2"
    matches = list(build.glob("*_results.xml"))
    if len(matches) != 1:
        raise ValueError("one fresh JUnit is required")
    xml = matches[0]
    vvp = build / "axis_register.vvp"
    log = work / "evaluator/pytest.log"
    exit_file = work / "evaluator/pytest.exit"
    result = cocotb_verdict(junit=xml.read_bytes(), runner_log=log.read_bytes(),
        expected_test_ids=COCOTB_SCOPES[0][5], runner_exit=int(exit_file.read_text().strip()))
    if result.get("verdict") != "PASS" or result.get("random_seed") != "20260924":
        raise ValueError("candidate native verdict/seed did not pass")
    text = log.read_text(encoding="utf-8", errors="replace")
    compile_lines = re.findall(r"(?m)^.*Running command: iverilog .*axis_register\.v\s*$", text)
    if len(compile_lines) != 1 or str(candidate) not in compile_lines[0]:
        raise ValueError("actual iverilog source command is not pinned candidate")
    if str(candidate).encode() not in vvp.read_bytes():
        raise ValueError("VVP does not identify candidate source")
    return {
        "schema": SCHEMA, "role": "DEV_SMOKE_NOT_MEMORY_TRANSFER",
        "work": str(work), "agent_stage_inventory": inventory,
        "actual_iverilog_command": compile_lines[0].split("Running command: ", 1)[1],
        "binding": binding, "action": edit, "native_verdict": result,
        "artifacts": {label: _ref(path) for label, path in (
            ("stage_receipt", stage_receipt), ("visible_buggy_source", visible),
            ("compiled_candidate_source", candidate), ("native_test", test),
            ("junit", xml), ("vvp", vvp), ("runner_log", log), ("runner_exit", exit_file),
            ("binder_code", Path(bind_skid_payload.__code__.co_filename)),
            ("verdict_code", Path(cocotb_verdict.__code__.co_filename)),
            ("auditor_code", Path(__file__)))
        },
        "claim_boundary": "researcher-assisted DEV primitive smoke, not a registered TEHM asset or Memory transfer",
    }


def audit_candidate(*, work: str | Path, output: str | Path) -> dict:
    out = Path(output).resolve(strict=False)
    if out.exists():
        raise ValueError(f"refusing to overwrite candidate audit: {out}")
    body = _assess(Path(work))
    body["digest"] = _sha(_json(body))
    out.write_bytes(_json(body))
    return {"valid": True, "output": str(out), "digest": body["digest"],
            "native_verdict": body["native_verdict"]["verdict"]}


def verify_candidate(receipt: str | Path) -> dict:
    path = Path(receipt).resolve(strict=True)
    saved = json.loads(path.read_text(encoding="utf-8"))
    digest = saved.pop("digest", None)
    errors = []
    if digest != _sha(_json(saved)):
        errors.append("receipt_digest")
    try:
        expected = _assess(Path(saved["work"]))
        if expected != saved:
            errors.append("raw_replay_drift")
    except (OSError, ValueError, KeyError) as exc:
        errors.append("raw_replay_error:" + type(exc).__name__)
    return {"valid": not errors, "errors": errors, "digest": digest}
