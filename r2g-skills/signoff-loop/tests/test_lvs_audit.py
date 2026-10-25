"""Three-layer audit and real fallback-shell negative controls; fixture data only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import lvs_audit as audit_module
from lvs_audit import audit


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database():
    nets = " ".join(f"net({i} name({name}))" for i, name in enumerate(("A", "Z", "VDD", "VSS"), 1))
    pins = " ".join(f"pin({i} name({name}))" for i, name in enumerate(("A", "Z", "VDD", "VSS"), 1))
    mos = "device(1 NMOS_VTL param(W 1) param(L .05) terminal(S 4) terminal(G 1) terminal(D 2) terminal(B 4))"
    circuits = f"circuit(INV {nets} {pins} {mos}) circuit(TOP {nets} {pins} circuit(1 INV pin(0 1)))"
    pairs = " ".join(f"net({i} {i} match)" for i in range(1, 5))
    pairs += " " + " ".join(f"pin({i} {i} match)" for i in range(4))
    return ("#%lvsdb-klayout\n"
            f"layout(top(TOP) class(NMOS_VTL MOS4) device(abstract NMOS_VTL terminal(G rect((0 0) (1 1)))) {circuits})\n"
            f"reference(class(NMOS_VTL MOS4) {circuits})\n"
            f"xref(circuit(INV INV match xref({pairs} device(1 1 match))) "
            f"circuit(TOP TOP match xref({pairs} circuit(1 1 match))))\n")


@pytest.fixture
def evidence(tmp_path):
    project = tmp_path / "project"
    lvs = project / "lvs"
    lvs.mkdir(parents=True)
    (project / "metadata.json").write_text(json.dumps({"top": "TOP"}))
    gds = project / "backend/RUN_fixture/final/6_final.gds"
    gds.parent.mkdir(parents=True)
    gds.write_bytes(b"test fixture layout bytes")
    (lvs / "6_lvs.lvsdb").write_text(database())
    (lvs / "6_lvs.log").write_text("CONGRATULATIONS! Netlists match.\nLVS finished\n")
    (lvs / "lvs_run.log").write_text("checker completed\n")
    (lvs / "audit_rule.lylvs").write_bytes(audit_module.SUPPORTED_DECK.read_bytes())
    (lvs / "audit_masters.cdl").write_text("fixture library\n")
    (lvs / "6_final.cdl").write_text("* fixture reference\n.SUBCKT TOP A Z\n+ VDD VSS\n.ENDS TOP\n")
    refresh(project)
    return project


def refresh(project, **overrides):
    lvs = project / "lvs"
    names = ("6_lvs.lvsdb", "6_lvs.log", "lvs_run.log", "audit_rule.lylvs", "audit_masters.cdl", "6_final.cdl")
    files = {name: sha(lvs / name) for name in names}
    receipt = {"schema_version": 1, "engine": "klayout", "exit_code": 0, "run_tag": "RUN_fixture",
               "gds_sha256": sha(project / "backend/RUN_fixture/final/6_final.gds"),
               "rule_sha256": files["audit_rule.lylvs"], "masters_sha256": files["audit_masters.cdl"],
               "tool_sha256": "a" * 64, "files": files, **overrides}
    (lvs / "lvs_execution.json").write_text(json.dumps(receipt))


def checked(project):
    return audit(project, project / "lvs")


def layer(value, name):
    return next(row for row in value["layers"] if row["id"] == name)


def test_complete_bound_fixture_passes_all_three_layers_without_trusting_summary(evidence):
    before = checked(evidence)
    assert before["verdict"] == "PASS"
    assert before["verification_level"] == "transistor_hierarchical"
    assert before["metrics"]["layout"]["devices"] == before["metrics"]["schematic"]["devices"] == 1
    assert before["metrics"]["layout"]["circuits"] == 2
    assert before["metrics"]["layout"]["nets"] == 8
    assert before["metrics"]["xref"] == {"circuit_match": 2, "net_match": 8, "pin_match": 8,
                                          "device_match": 1, "instance_match": 1}
    (evidence / "reports").mkdir()
    (evidence / "reports/lvs.json").write_text('{"status":"fail","three_layer_audit":{"verdict":"PASS"}}')
    assert checked(evidence) == before
    assert len(before["policy_sha256"]) == 64
    for row in before["evidence_files"]:
        assert not Path(row["path"]).is_absolute()
        assert sha(evidence / row["path"]) == row["sha256"]


@pytest.mark.parametrize("content", ["<lvs/>", "<status>clean</status>", "#%lvsdb-klayout\ncircuits match\n",
                                   "#%lvsdb-klayout\nlayout() reference()", "#%lvsdb-klayout\nJ() K() L()"])
def test_empty_xml_partial_match_and_unsupported_database_are_unknown(evidence, content):
    (evidence / "lvs/6_lvs.lvsdb").write_text(content)
    refresh(evidence)
    assert checked(evidence)["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("content", ["cell INV matches\n", "no matching net\nCONGRATULATIONS! Netlists match.\n",
                                   "Netlists match.\nERROR: Netlists don't match\n"])
def test_partial_or_contradictory_match_text_cannot_pass(evidence, content):
    (evidence / "lvs/6_lvs.log").write_text(content)
    refresh(evidence)
    value = checked(evidence)
    assert value["verdict"] != "PASS"
    assert layer(value, "final_result")["verdict"] == ("UNKNOWN" if content.startswith("cell") else "FAIL")


def test_post_compare_error_prevents_accepting_a_partial_success(evidence):
    with (evidence / "lvs/6_lvs.log").open("a") as stream:
        stream.write("ERROR: report writer failed after comparison\n")
    refresh(evidence)
    assert layer(checked(evidence), "final_result")["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("exit_code", [1, 124, -11, None, "0", False])
def test_match_with_unsuccessful_or_missing_exit_code_is_unknown(evidence, exit_code):
    refresh(evidence, exit_code=exit_code)
    assert layer(checked(evidence), "final_result")["verdict"] == "UNKNOWN"


def test_missing_execution_receipt_cannot_reuse_a_legacy_clean(evidence):
    (evidence / "lvs/lvs_execution.json").unlink()
    value = checked(evidence)
    assert value["verdict"] == "UNKNOWN"
    assert layer(value, "quantitative")["verdict"] == "PASS"


@pytest.mark.parametrize("path,content", [
    ("lvs/lvs_execution.json", "[]"), ("lvs/lvs_execution.json", "null"),
    ("lvs/lvs_execution.json", "{"), ("metadata.json", "[]"),
])
def test_malformed_supporting_evidence_returns_unknown_without_raising(evidence, path, content):
    (evidence / path).write_text(content)
    assert checked(evidence)["verdict"] == "UNKNOWN"


def test_missing_supported_rule_returns_unknown_without_raising(evidence, tmp_path, monkeypatch):
    monkeypatch.setattr(audit_module, "SUPPORTED_DECK", tmp_path / "absent.lylvs")
    assert checked(evidence)["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("path", ["lvs/6_lvs.lvsdb", "lvs/6_lvs.log", "lvs/6_final.cdl",
                                  "lvs/audit_masters.cdl", "backend/RUN_fixture/final/6_final.gds"])
def test_changed_raw_evidence_or_layout_is_stale(evidence, path):
    with (evidence / path).open("ab") as stream:
        stream.write(b"\n# changed")
    assert checked(evidence)["verdict"] == "STALE"


@pytest.mark.parametrize("replacement", ["device(1 1 mismatch)", "device(1 1 warning)", "device(8 1 match)", ""])
def test_unmatched_unknown_or_incomplete_device_pairs_cannot_pass(evidence, replacement):
    path = evidence / "lvs/6_lvs.lvsdb"
    path.write_text(path.read_text().replace("device(1 1 match)", replacement))
    refresh(evidence)
    value = checked(evidence)
    assert value["verdict"] != "PASS"
    assert layer(value, "quantitative")["verdict"] != "PASS"


def test_same_scope_device_count_mismatch_fails(evidence):
    path = evidence / "lvs/6_lvs.lvsdb"
    path.write_text(path.read_text().replace("device(1 NMOS_VTL", "device(2 NMOS_VTL terminal(S 4)) device(1 NMOS_VTL", 1))
    refresh(evidence)
    assert layer(checked(evidence), "quantitative")["verdict"] == "FAIL"


@pytest.mark.parametrize("warning", ["Must-connect subnets of VDD must be connected further up in the hierarchy",
                                    "unknown extraction warning"])
def test_extraction_warnings_preserve_mos_evidence_but_block_technical_pass(evidence, warning):
    path = evidence / "lvs/6_lvs.lvsdb"
    path.write_text(path.read_text().replace("layout(", f"layout(message(warning description('{warning}')) ", 1))
    refresh(evidence)
    value = checked(evidence)
    assert value["verification_level"] == "transistor_hierarchical"
    assert layer(value, "quantitative")["verdict"] == "PASS"
    assert layer(value, "technical")["verdict"] == "UNKNOWN"


def test_blackbox_diagnostic_blocks_even_when_counts_match(evidence):
    with (evidence / "lvs/6_lvs.log").open("a") as stream:
        stream.write("Circuit INV is a black box\n")
    refresh(evidence)
    assert "blackbox_or_ignored_structure_reported" in layer(checked(evidence), "technical")["reasons"]


@pytest.mark.parametrize("severity,description,expected", [
    ("warning", "Matching nets A and B from an ambiguous group of nets", "PASS"),
    ("info", "Matching nets A vs. B following an ambiguous match", "PASS"),
    ("warning", "Device parameter ignored during comparison", "UNKNOWN"),
])
def test_comparison_warnings_are_classified_conservatively(evidence, severity, description, expected):
    path = evidence / "lvs/6_lvs.lvsdb"
    path.write_text(path.read_text().replace("circuit(INV INV match xref(",
                   f"circuit(INV INV match log(entry({severity} description('{description}'))) xref("))
    refresh(evidence)
    assert layer(checked(evidence), "technical")["verdict"] == expected


def test_paired_top_port_label_swap_fails(evidence):
    path = evidence / "lvs/6_lvs.lvsdb"
    text = path.read_text()
    marker = text.rindex("circuit(TOP TOP match")
    text = text[:marker] + text[marker:].replace("pin(0 0 match)", "pin(0 1 match)").replace("pin(1 1 match)", "pin(1 0 match)")
    path.write_text(text)
    refresh(evidence)
    assert "top_pin_label_mismatch" in layer(checked(evidence), "technical")["reasons"]
    assert checked(evidence)["verdict"] == "FAIL"


def test_port_removed_from_both_simplified_netlists_is_not_silently_certified(evidence):
    path = evidence / "lvs/6_final.cdl"
    path.write_text(path.read_text().replace("+ VDD VSS", "+ UNUSED VDD VSS"))
    refresh(evidence)
    value = checked(evidence)
    assert layer(value, "quantitative")["verdict"] == "PASS"
    assert layer(value, "technical")["verdict"] == "UNKNOWN"
    assert layer(value, "technical")["evidence"]["source_top_port_coverage"] == {
        "layout": {"missing": ["unused"], "extra": []},
        "schematic": {"missing": ["unused"], "extra": []},
    }


def test_undefined_device_class_cannot_hide_among_valid_mos(evidence):
    path = evidence / "lvs/6_lvs.lvsdb"
    path.write_text(path.read_text().replace("device(1 NMOS_VTL", "device(1 NOT_DEFINED", 1))
    refresh(evidence)
    assert checked(evidence)["verdict"] == "UNKNOWN"


def test_unknown_deck_and_ignored_circuits_are_not_certified(evidence):
    path = evidence / "lvs/audit_rule.lylvs"
    path.write_text(path.read_text().replace("tstart = Time.now", 'blank_circuit("*")\ntstart = Time.now'))
    refresh(evidence)
    value = checked(evidence)
    assert "unregistered_extraction_rule" in layer(value, "technical")["reasons"]
    assert "rule_contains_unverified_comparison_exclusions" in layer(value, "technical")["reasons"]


def test_symlink_to_external_evidence_is_not_read(evidence, tmp_path):
    path = evidence / "lvs/6_lvs.lvsdb"
    outside = tmp_path / "outside.lvsdb"
    outside.write_text(database())
    path.unlink()
    path.symlink_to(outside)
    value = checked(evidence)
    assert value["verdict"] == "UNKNOWN"
    assert not any(row["path"] == "lvs/6_lvs.lvsdb" for row in value["evidence_files"])


def test_symlinked_evidence_directory_is_not_traversed(evidence, tmp_path):
    path = evidence / "lvs"
    outside = tmp_path / "external_lvs"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=True)
    value = checked(evidence)
    assert value["verdict"] == "UNKNOWN" and value["evidence_files"] == []


def test_oversized_evidence_is_unknown(evidence, monkeypatch):
    monkeypatch.setattr(audit_module, "MAX_BYTES", 8)
    assert checked(evidence)["verdict"] == "UNKNOWN"


def test_extractor_downgrades_unsupported_legacy_clean_without_hiding_raw_status(evidence):
    (evidence / "lvs/lvs_execution.json").unlink()
    script = Path(audit_module.__file__).with_name("extract_lvs.py")
    output = evidence / "reports/lvs.json"
    run = subprocess.run([sys.executable, str(script), str(evidence), str(output)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    value = json.loads(output.read_text())
    assert value["status"] == "unverified" and value["raw_status"] == "clean"
    assert value["mismatch_count"] is None
    assert value["three_layer_audit"]["verdict"] == "UNKNOWN"


@pytest.fixture
def fallback(tmp_path):
    gds, cdl, out = tmp_path / "test.gds", tmp_path / "test.cdl", tmp_path / "fallback"
    gds.write_bytes(b"fixture GDS")
    cdl.write_text(".subckt TOP A Z VDD VSS\nM1 Z A VSS VSS NMOS_VTL w=1 l=.05\n.ends TOP\n")
    out.mkdir()
    for name in ("layout.cir", "schematic.cir", "netgen.rpt"):
        (out / name).write_text("Final result: Circuits match uniquely.\n")
    klayout = tmp_path / "klayout"
    klayout.write_text(f"#!{sys.executable}\n" + '''import os,sys
from pathlib import Path
if '-v' in sys.argv: print('fixture klayout'); raise SystemExit
if not os.environ.get('NO_EXTRACT_OUTPUT'):
    for arg in sys.argv:
        if arg.startswith(('extracted_out=', 'schematic_out=')):
            Path(arg.split('=',1)[1]).write_text('.subckt TOP A Z VDD VSS\\nM1 Z A VSS VSS NMOS_VTL w=1 l=.05\\n.ends TOP\\n')
raise SystemExit(int(os.environ.get('TEST_EXTRACT_RC','0')))
''')
    netgen = tmp_path / "netgen"
    netgen.write_text(f"#!{sys.executable}\n" + '''import os,sys
from pathlib import Path
if 'quit' in sys.argv: print('fixture netgen'); raise SystemExit
Path('netgen.rpt').write_text('Final result: Circuits match uniquely.\\n')
raise SystemExit(int(os.environ.get('TEST_NETGEN_RC','0')))
''')
    klayout.chmod(0o755); netgen.chmod(0o755)
    script = Path(audit_module.__file__).parents[1] / "flow/lvs_netgen_fallback.sh"
    def run(**extra):
        process = subprocess.run(["bash", str(script), str(gds), str(cdl), "TOP", str(out)],
                                 env={**os.environ, "KLAYOUT_CMD": str(klayout), "NETGEN_EXE": str(netgen), **extra},
                                 capture_output=True, text=True, timeout=10)
        return process, json.loads((out / "netgen_fallback.json").read_text())
    return run, out


def test_failed_fallback_extraction_cannot_reuse_old_nonempty_outputs(fallback):
    run, _ = fallback
    process, value = run(TEST_EXTRACT_RC="1", NO_EXTRACT_OUTPUT="1")
    assert process.returncode != 0
    assert value["verdict"] == "no_verdict" and value["extraction_exit_code"] == 1
    assert value["comparison_exit_code"] is None


def test_fallback_nonzero_compare_cannot_accept_a_match_line(fallback):
    run, _ = fallback
    process, value = run(TEST_NETGEN_RC="7")
    assert process.returncode != 0
    assert value["verdict"] == "no_verdict" and value["comparison_exit_code"] == 7


def test_fallback_success_records_only_its_isolated_current_outputs(fallback):
    run, out = fallback
    process, value = run()
    assert process.returncode == 0, process.stderr
    assert value["verdict"] == "match"
    assert value["extraction_exit_code"] == value["comparison_exit_code"] == 0
    assert value["attempt_directory"].startswith("attempt.")
    for name, checksum in value["output_sha256"].items():
        assert sha(out / value["attempt_directory"] / name) == checksum
    for name, checksum in value["input_sha256"].items():
        assert sha(Path(name)) == checksum
