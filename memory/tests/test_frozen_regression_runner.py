"""Regression provenance units; no test run is fabricated as hardware evidence."""
import pytest
from scripts.run_frozen_regression import corpus, junit_totals, terminal_result, write_new


def test_corpus_detects_actual_source_edit(tmp_path):
    source = tmp_path / "example.py"
    source.write_text("x=1\n")
    before = corpus(tmp_path)
    assert before["file_count"] == 1
    source.write_text("x=2\n")
    assert corpus(tmp_path)["corpus_digest"] != before["corpus_digest"]


@pytest.mark.parametrize("root", ["testsuite", "testsuites"])
def test_junit_single_or_multi_suite(tmp_path, root):
    path = tmp_path / "unit.xml"
    suite = '<testsuite tests="2" failures="0" errors="0" skipped="0"/>'
    path.write_text(suite if root == "testsuite" else "<testsuites>" + suite + "</testsuites>")
    assert junit_totals(path) == dict(tests=2, failures=0, errors=0, skipped=0)


@pytest.mark.parametrize("kind", ["source_drift", "exit_failure", "missing_tests", "failures", "errors", "skipped"])
def test_terminal_gate_fails_closed(kind):
    frozen = {"files": ["source"], "corpus_digest": "original"}
    current = dict(frozen)
    totals = dict(tests=1600, failures=0, errors=0, skipped=0)
    exit_code = 0
    if kind == "source_drift": current["corpus_digest"] = "edited"
    elif kind == "exit_failure": exit_code = 1
    elif kind == "missing_tests": totals["tests"] = 0
    else: totals[kind] = 1
    assert terminal_result(frozen, current, totals, exit_code, 1600)["status"] == "FAIL"


def test_success_requires_all_provenance_gates():
    corpus = {"corpus_digest": "same"}
    assert terminal_result(corpus, corpus, dict(tests=1600, failures=0, errors=0, skipped=0), 0, 1600)["status"] == "PASS"


def test_terminal_artifacts_cannot_be_overwritten(tmp_path):
    path = tmp_path / "terminal.json"
    write_new(path, {"status": "FAIL"})
    with pytest.raises(FileExistsError): write_new(path, {"status": "PASS"})
    assert '"FAIL"' in path.read_text()
