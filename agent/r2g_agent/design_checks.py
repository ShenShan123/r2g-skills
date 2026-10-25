"""Bounded source inspection and two fixed, cheap diagnostic recipes.

These checks do not simulate an arbitrary testbench, edit an input, validate
functionality or grant permission for a physical flow. The existing pipeline
remains the authority for full synthesis, signoff and delivery.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import selectors
import signal
import subprocess
import time
import uuid

from . import frontend, project
from .input_snapshots import digest, manifest, read_tree, write_json
from .paths import r2g_home

CHECKS = ("compile", "synthesis_structure")
MAX_BYTES = 16 * 1024 * 1024
MAX_SOURCES = 128
CHECK_SECONDS = 10
MAX_LOG_BYTES = 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SAFE_PATH = re.compile(r"[A-Za-z0-9_.\-/]+\Z")
_PRIMITIVES = frozenset("and nand or nor xor xnor not buf bufif0 bufif1 notif0 notif1 tran rtran tranif0 tranif1 pullup pulldown".split())


def _load(path: str):
    root = Path(path).expanduser().absolute()
    if not root.is_dir():
        raise ValueError("select the design's source directory, not a single file")
    files = read_tree(root)  # Descriptor-based reads reject links and changing files.
    if sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError("cheap diagnostic input exceeds 16 MiB")
    sources = {name: data.decode("utf-8", errors="replace") for name, data in files.items()
               if Path(name).suffix.lower() in project.HDL_SUFFIXES}
    if not sources or len(sources) > MAX_SOURCES:
        raise ValueError("cheap diagnostics require between 1 and 128 HDL files")
    # Preprocessors accept headers with arbitrary suffixes. Scan the complete
    # local include closure, so a .inc cannot hide external file access from
    # the same checks applied to the entry HDL files.
    pending = list(sources)
    while pending:
        name = pending.pop()
        for ref in re.findall(r'`include\s+"([^"\n]+)"', project._strip_comments(sources[name])):
            resolved = _reference(files, name, ref)
            if resolved and resolved not in sources:
                sources[resolved] = files[resolved].decode("utf-8", errors="replace")
                pending.append(resolved)
                if len(sources) > MAX_SOURCES:
                    raise ValueError("diagnostic include closure exceeds 128 source files")
    return root, files, sources


def _reference(files, source: str, reference: str):
    if not reference or reference.startswith("/") or "\\" in reference:
        return None
    for candidate in (posixpath.normpath(posixpath.join(posixpath.dirname(source), reference)),
                      posixpath.normpath(reference)):
        if candidate != ".." and not candidate.startswith("../") and candidate in files:
            return candidate
    return None


def _inspect(root, files, sources, top: str):
    definitions, used, includes, data_files, benches = {}, {}, [], [], []
    for name, raw in sources.items():
        text = project._strip_comments(raw)
        if re.search(r"(^|/)(test|tests|tb|testbench)(/|$)|(^|/)(tb_|test_)|_tb\.", name, re.I) or "$finish" in text:
            benches.append(name)
        for ref in re.findall(r'`include\s+"([^"\n]+)"', text):
            resolved = _reference(files, name, ref)
            includes.append({"source": name, "reference": ref, "resolved": resolved,
                             "status": "present" if resolved else "unresolved"})
        for ref, _ in frontend.readmem_references(raw):
            resolved = _reference(files, name, ref)
            data_files.append({"source": name, "reference": ref, "resolved": resolved,
                               "status": "present" if resolved else "unresolved"})
        for chunk in re.split(r"\bmodule\s+", text)[1:]:
            module = re.match(r"([A-Za-z_][\w$]*)", chunk)
            if not module:
                continue
            body = chunk.split("endmodule", 1)[0]
            module = module[1]
            signature = body.split(";", 1)[0]
            definitions.setdefault(module, []).append({"file": name, "signature": ("module " + signature)[:2000]})
            uses = {kind for kind, _ in project._INST_RE.findall(project._strip_param_overrides(body))
                    if kind not in project._KEYWORDS and kind not in _PRIMITIVES}
            used.setdefault(module, set()).update(uses)
    all_used = set().union(*used.values()) if used else set()
    candidates = sorted(set(definitions) - all_used)
    if not top and "design.json" in files:
        metadata = json.loads(files["design.json"])
        top = metadata.get("top") or ""
    if not top:
        if len(candidates) == 1:
            top = candidates[0]
        elif root.name in candidates:
            top = root.name
    if top and (not isinstance(top, str) or not _IDENTIFIER.fullmatch(top)):
        raise ValueError("diagnostic top must be a simple Verilog identifier")
    missing = sorted(all_used - set(definitions))
    findings = []
    if not top:
        findings.append("top_ambiguous")
    elif top not in definitions:
        findings.append("top_not_defined")
    if any(len(entries) > 1 for entries in definitions.values()):
        findings.append("duplicate_module_definitions")
    if any(x["status"] == "unresolved" for x in includes):
        findings.append("unresolved_include")
    if any(x["status"] == "unresolved" for x in data_files):
        findings.append("unresolved_data_dependency")
    return {"schema_version": 1, "source": str(root), "input_snapshot": manifest(files),
            "hdl_files": sorted(sources), "source_lines": sum(len(t.splitlines()) for t in sources.values()),
            "modules": [{"name": name, **entries[0], "dependencies": sorted(used.get(name, []))}
                        for name, entries in sorted(definitions.items())][:64],
            "top": top or None, "top_candidates": candidates[:64], "findings": findings,
            "missing_module_candidates": missing[:64], "include_dependencies": includes[:64],
            "data_dependencies": data_files[:64], "testbench_candidates": benches[:32],
            "analysis": "static heuristics; compiler hierarchy checks are authoritative",
            "functional_verification": "not_run", "execution_authorized": False,
            "next_checks": list(CHECKS) if not findings else ["resolve_input_findings"],
            "selection_evidence": ["top/interface/dependency candidates", "source size", "testbench presence only"],
            "limits": {"bytes": MAX_BYTES, "hdl_files": MAX_SOURCES}}


def inspect_design(path: str, top: str = "") -> dict:
    """Observe a source candidate; no compilation, execution, or source mutation."""
    root, files, sources = _load(path)
    return _inspect(root, files, sources, top)


def _classification(log: str, rc: int):
    if rc == 0:
        return None
    if rc == 124:
        return "timeout"
    if rc == 125:
        return "diagnostic_output_limit"
    if re.search(r"unknown module|module .*not part|module .*not found|unable to find.*module|not found in the design", log, re.I):
        return "missing_module"
    if re.search(r"unable to open|can't open|cannot open|include file .*not found", log, re.I):
        return "missing_input"
    if re.search(r"syntax error|unexpected|malformed", log, re.I):
        return "syntax_error"
    if re.search(r"unable to bind parameter|dimensions must be constant|error.*elaboration", log, re.I):
        return "elaboration_error"
    return "structural_error" if rc else None


def _run_fixed(command: list[str], copied: Path, workspace: Path, log: Path) -> int:
    """One host recipe with bounded output, deadline and descendant lifetime."""
    process = subprocess.Popen(command, cwd=copied, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True, env={"PATH": os.defpath, "HOME": str(workspace),
        "TMPDIR": str(workspace), "LANG": "C"})
    end, written, rc = time.monotonic() + CHECK_SECONDS, 0, None
    try:
        with log.open("wb") as stream, selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() and rc is None:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    rc = 124
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    room = MAX_LOG_BYTES - written
                    stream.write(chunk[:room])
                    written += min(len(chunk), room)
                    if len(chunk) > room:
                        rc = 125
                        break
            if rc is None:
                try:
                    rc = process.wait(timeout=max(0.001, end - time.monotonic()))
                except subprocess.TimeoutExpired:
                    rc = 124
    finally:
        # A wrapper may exit while its descendants still hold an output pipe.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
    return rc


def check_design(path: str, top: str, check: str = "compile", *, inventory: dict | None = None) -> dict:
    """Execute a fixed compiler recipe in a private source copy, at most 10 s.

    Not a simulation or equivalence oracle. The model cannot supply commands,
    flags, testbench code, tools, environment variables or output locations.
    """
    if check not in CHECKS:
        raise ValueError("check must be compile or synthesis_structure")
    root, files, sources = _load(path)
    observed = _inspect(root, files, sources, top)
    refusal = list(observed["findings"])
    if any(not _SAFE_PATH.fullmatch(name) for name in files):
        refusal.append("unsupported_filename_for_fixed_recipe")
    # Arbitrary simulation and compile-time file access outside the copied
    # input are not capabilities of these diagnostic recipes.
    for name, data in files.items():
        raw = data.decode("utf-8", errors="replace")
        if re.search(r'\$(?:system|fopen|fwrite|fdisplay|fread|fscanf|readmemh|readmemb)\b', raw):
            refusal.append("source_needs_separate_file_io_policy:" + name)
        if "``" in raw or re.search(r'\$\s*`', raw):
            refusal.append("macro_token_construction_needs_separate_policy:" + name)
        if re.search(r'`include\s+(?!")', project._strip_comments(raw)):
            refusal.append("dynamic_include_needs_separate_policy:" + name)
        if re.search(r'`include\s+"(?:/|[^"\n]*\.\./)', raw):
            refusal.append("external_include_not_allowed:" + name)
    if refusal:
        return {"schema_version": 1, "status": "unsupported_input", "check": check,
                "findings": sorted(set(refusal)), "input_snapshot_id": observed["input_snapshot"]["sha256"],
                "full_flow_ready": False, "functional_verification": "not_run"}
    from . import capabilities, task_skills
    tool_name = "iverilog" if check == "compile" else "yosys"
    inventory = inventory if inventory is not None else capabilities.tool_inventory((tool_name,))
    skill_id = "compile_failure" if check == "compile" else "synthesis_diagnosis"
    permission = task_skills.validate_diagnostic(skill_id, check, stage="input", capabilities=inventory)
    if not permission["allowed"]:
        return {"schema_version": 1, "status": "blocked", "check": check,
                "failure_class": "environment", "task_permission": permission,
                "reasons": permission["refusals"], "functional_verification": "not_run", "full_flow_ready": False}
    tool = inventory.get("tools", {}).get(tool_name, {})
    executable = tool.get("path")
    if not tool.get("usable") or not tool.get("sha256") or not executable or not Path(executable).is_file():
        return {"schema_version": 1, "status": "blocked", "check": check,
                "failure_class": "environment", "reasons": ["required_tool_unavailable:" + tool_name],
                "functional_verification": "not_run", "full_flow_ready": False}
    checker = {"path": str(Path(executable).resolve()),
               "sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest()}
    if checker["sha256"] != tool["sha256"]:
        return {"schema_version": 1, "status": "unverified", "check": check,
                "failure_class": "tool_version_changed", "functional_verification": "not_run",
                "full_flow_ready": False}
    checker["path"] = str(executable)  # Preserve the host's wrapper lookup semantics.
    check_id = uuid.uuid4().hex
    workspace = r2g_home() / "design-checks" / check_id
    workspace.mkdir(mode=0o700, parents=True, exist_ok=False)
    copied = workspace / "source"
    copied.mkdir(mode=0o700)
    for name, data in files.items():
        target = copied / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    included = {_reference(files, name, ref) for name, raw in sources.items()
                for ref in re.findall(r'`include\s+"([^"\n]+)"', project._strip_comments(raw))}
    input_files = sorted(name for name in sources if Path(name).suffix.lower() in (".v", ".sv") and name not in included)
    if not input_files:
        return {"schema_version": 1, "status": "unsupported_input", "check": check,
                "findings": ["no_standalone_compilation_unit"], "full_flow_ready": False,
                "functional_verification": "not_run"}
    directories = sorted({".", *(posixpath.dirname(name) or "." for name in sources)})
    if check == "compile":
        command = [checker["path"], "-g2012", "-tnull", "-s", observed["top"]]
        command += [part for directory in directories for part in ("-I", directory)]
        command += ["./" + name for name in input_files]
    else:
        script = "read_verilog -sv " + " ".join("-I./" + directory for directory in directories) + " " + " ".join("./" + name for name in input_files)
        script += "; hierarchy -check -top " + observed["top"] + "; proc; opt_clean; check -assert; stat"
        command = [checker["path"], "-Q", "-T", "-p", script]
    log = workspace / "stdout.log"
    started = time.time()
    rc = _run_fixed(command, copied, workspace, log)
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 8000))
        output = stream.read(8000).decode(errors="replace")
    with log.open("rb") as stream:
        log_digest = hashlib.sha256()
        while chunk := stream.read(65536):
            log_digest.update(chunk)
        log_hash = log_digest.hexdigest()
    unchanged = manifest(read_tree(root)) == observed["input_snapshot"]
    tool_unchanged = checker["sha256"] == hashlib.sha256(Path(checker["path"]).read_bytes()).hexdigest()
    current_permission = task_skills.validate_diagnostic(skill_id, check, stage="input", capabilities=inventory)
    policy_unchanged = current_permission == permission
    result = {"schema_version": 1, "check_id": check_id, "check": check, "exit_code": rc,
              "status": "passed" if rc == 0 and unchanged and tool_unchanged and policy_unchanged else "failed" if rc else "unverified",
              "failure_class": _classification(output, rc), "command": command,
              "top": observed["top"], "source": str(root), "input_snapshot_id": observed["input_snapshot"]["sha256"],
              "inputs_unchanged": unchanged, "checker_unchanged": tool_unchanged, "checker": checker,
              "task_permission": permission, "task_contract_unchanged": policy_unchanged,
              "started": started, "elapsed_seconds": round(time.time() - started, 3),
              "timeout_seconds": CHECK_SECONDS, "inventory_id": inventory.get("inventory_id"),
              "log_tail": output, "log_sha256": log_hash, "log_limit_bytes": MAX_LOG_BYTES,
              "report": str(workspace / "result.json"), "functional_verification": "not_run",
              "full_flow_ready": False, "not_checked": ["functional correctness", "technology mapping", "physical design", "signoff", "dataset"]}
    result["evidence_id"] = digest({k: v for k, v in result.items() if k not in ("log_tail", "report")})
    write_json(workspace / "result.json", result)
    return result
