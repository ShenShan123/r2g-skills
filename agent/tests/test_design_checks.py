"""Input evidence and real cheap compiler behavior, independent of model prose."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from r2g_agent import design_checks as checks


def source(tmp_path, text="module top(input a, output y); assign y = a; endmodule\n"):
    path = tmp_path / "rtl"
    path.mkdir()
    (path / "top.v").write_text(text)
    return path


def inventory(tool):
    executable = shutil.which(tool)
    if not executable:
        pytest.skip("real compiler not installed: " + tool)
    return {"inventory_id": "isolated-compiler-test", "tools": {tool: {
        "usable": True, "path": executable,
        "sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest()}}}


def test_candidate_inspection_records_interface_dependencies_and_source_version(tmp_path):
    path = source(tmp_path, "module top(input a, output y);\nchild u(.a(a),.y(y));\nendmodule\n")
    report = checks.inspect_design(str(path), "top")
    assert report["top"] == "top"
    assert report["missing_module_candidates"] == ["child"]
    assert "input a" in report["modules"][0]["signature"]
    before = report["input_snapshot"]["sha256"]
    (path / "top.v").write_text("module top; endmodule\n")
    assert checks.inspect_design(str(path), "top")["input_snapshot"]["sha256"] != before
    assert report["functional_verification"] == "not_run"


def test_ambiguous_top_requires_selection(tmp_path):
    path = source(tmp_path, "module one; endmodule\nmodule two; endmodule\n")
    result = checks.check_design(str(path), "", inventory={})
    assert result["status"] == "unsupported_input"
    assert "top_ambiguous" in result["findings"]


def test_symlinked_source_is_not_followed(tmp_path):
    path = source(tmp_path)
    secret = tmp_path / "outside.v"
    secret.write_text("module secret; endmodule")
    (path / "extra.v").symlink_to(secret)
    with pytest.raises((ValueError, OSError)):
        checks.inspect_design(str(path), "top")


@pytest.mark.parametrize("construct", ['$system("touch outside");', '$readmemh("/outside/key", mem);', '`include "../outside.v"'])
def test_external_io_and_external_includes_do_not_execute(tmp_path, construct):
    path = source(tmp_path, "module top;\n" + construct + "\nendmodule\n")
    result = checks.check_design(str(path), "top", inventory={})
    assert result["status"] == "unsupported_input"
    assert result["full_flow_ready"] is False
    assert not (tmp_path / "home").exists()


def test_missing_compiler_is_environment_not_rtl_failure(tmp_path):
    result = checks.check_design(str(source(tmp_path)), "top", inventory={"tools": {}})
    assert result["status"] == "blocked"
    assert result["failure_class"] == "environment"


def test_changed_compiler_version_is_not_relabelled_current(tmp_path):
    path = source(tmp_path)
    inv = inventory("iverilog")
    inv["tools"]["iverilog"]["sha256"] = "0" * 64
    result = checks.check_design(str(path), "top", inventory=inv)
    assert result["status"] == "unverified"
    assert result["failure_class"] == "tool_version_changed"


def test_real_compile_does_not_claim_functional_or_full_flow_success(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_HOME", str(tmp_path / "home"))
    path = source(tmp_path)
    before = (path / "top.v").read_bytes()
    report = checks.check_design(str(path), "top", inventory=inventory("iverilog"))
    assert report["status"] == "passed"
    assert report["functional_verification"] == "not_run"
    assert report["full_flow_ready"] is False
    assert report["inputs_unchanged"] and report["checker_unchanged"]
    assert (path / "top.v").read_bytes() == before
    assert json.loads(Path(report["report"]).read_text())["evidence_id"] == report["evidence_id"]


def test_real_missing_module_does_not_reach_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_HOME", str(tmp_path / "home"))
    path = source(tmp_path, "module top(input a, output y);\nmissing_dep u(.a(a),.y(y));\nendmodule\n")
    report = checks.check_design(str(path), "top", inventory=inventory("iverilog"))
    assert report["status"] == "failed"
    assert report["failure_class"] == "missing_module"
    assert not report["full_flow_ready"]


def test_real_yosys_structure_check_is_not_technology_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_HOME", str(tmp_path / "home"))
    report = checks.check_design(str(source(tmp_path)), "top", "synthesis_structure", inventory=inventory("yosys"))
    assert report["status"] == "passed"
    assert "technology mapping" in report["not_checked"]
    assert report["functional_verification"] == "not_run"


@pytest.mark.parametrize("construct", ['`include "/outside/private.v"', '$readmemh("/outside/key", mem);', '`include MACRO_HEADER'])
def test_non_hdl_include_closure_cannot_hide_external_reads(tmp_path, monkeypatch, construct):
    monkeypatch.setenv("R2G_HOME", str(tmp_path / "home"))
    path = source(tmp_path, '`include "outer.inc"\nmodule top; endmodule\n')
    (path / "outer.inc").write_text('`include "inner.inc"\n')
    (path / "inner.inc").write_text(construct + "\n")
    monkeypatch.setattr(checks.subprocess, "Popen", lambda *a, **kw: pytest.fail("refused source executed"))
    result = checks.check_design(str(path), "top", inventory={})
    assert result["status"] == "unsupported_input"


def test_included_verilog_is_not_compiled_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_HOME", str(tmp_path / "home"))
    path = source(tmp_path, '`include "child.v"\nmodule top(input a, output y); child u(.a(a),.y(y)); endmodule\n')
    (path / "child.v").write_text("module child(input a, output y); assign y=a; endmodule\n")
    result = checks.check_design(str(path), "top", inventory=inventory("iverilog"))
    assert result["status"] == "passed"
    assert "./child.v" not in result["command"]


def test_task_contract_denial_prevents_process_even_with_usable_compiler(tmp_path, monkeypatch):
    from r2g_agent import task_skills
    path = source(tmp_path)
    inv = inventory("iverilog")
    monkeypatch.setattr(task_skills, "load_skill", lambda _: {"id": "compile_failure"})
    monkeypatch.setattr(checks.subprocess, "Popen", lambda *a, **kw: pytest.fail("denied diagnostic executed"))
    result = checks.check_design(str(path), "top", inventory=inv)
    assert result["status"] == "blocked"
    assert "diagnostic_not_allowed_by_task_contract" in result["reasons"]


def test_contract_change_during_check_invalidates_success(tmp_path, monkeypatch):
    from r2g_agent import task_skills
    original = task_skills.load_skill
    path = source(tmp_path)
    inv = inventory("iverilog")
    calls = []
    def changed(skill):
        contract = original(skill)
        calls.append(skill)
        if len(calls) > 1:
            contract["version"] += 1
        return contract
    monkeypatch.setattr(task_skills, "load_skill", changed)
    result = checks.check_design(str(path), "top", inventory=inv)
    assert result["status"] == "unverified" and not result["task_contract_unchanged"]


def test_successful_log_cannot_introduce_failure_class():
    assert checks._classification("syntax error printed by a successful $display", 0) is None


def test_bounded_runner_stops_output_and_records_no_pass(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(checks, "MAX_LOG_BYTES", 1024)
    log = tmp_path / "stdout.log"
    rc = checks._run_fixed([sys.executable, "-c", "import os; os.write(1,b'x'*32768)"], tmp_path, tmp_path, log)
    assert rc == 125 and log.stat().st_size == 1024
    assert checks._classification("", rc) == "diagnostic_output_limit"
