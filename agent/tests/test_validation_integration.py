"""Acceptance consumers cannot turn a recorded clean/PASS into new authority."""
import hashlib
import json
import tarfile

import pytest

from r2g_agent import housekeeping, package, pipeline, server, tools, validation
from r2g_agent.jobs import JobStore
from r2g_agent.input_snapshots import capture
from r2g_agent.artifact_snapshots import capture as capture_outputs
from r2g_agent.pipeline import OWNER, Pipeline


def observed(verdict="PASS", total=2):
    return {"schema_version": 1, "policy_sha256": "b" * 64, "verdict": verdict,
            "three_layer": {key: {"verdict": verdict} for key in validation.LAYER_IDS},
            "checks": {**{key: {"verdict": verdict} for key in validation.REQUIRED},
                       "dataset": {"verdict": "PASS", "metrics": {"passed": total, "total": total}}},
            "claims": [], "evidence_files": []}


def reports(project, drc=0, lvs=0):
    for name, body in (("drc", {"status": "clean", "total_violations": drc}),
                       ("lvs", {"status": "clean", "mismatch_count": lvs}),
                       ("rcx", {"status": "complete"})):
        path = project / "reports" / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body))


@pytest.mark.parametrize("count", [None, False, 9])
def test_clean_with_missing_boolean_or_nonzero_count_cannot_be_accepted(tmp_path, monkeypatch, count):
    reports(tmp_path, drc=count)
    monkeypatch.setattr(validation, "review", lambda *a, **kw: observed())
    result = pipeline.signoff_verdict(tmp_path)
    assert result["dual_clean"] is False and "infrastructure_error" in result


def test_raw_clean_cannot_override_unknown_review(tmp_path, monkeypatch):
    reports(tmp_path)
    monkeypatch.setattr(validation, "review", lambda *a, **kw: observed("UNKNOWN"))
    result = pipeline.signoff_verdict(tmp_path)
    assert not result["dual_clean"] and result["validation"]["verdict"] == "UNKNOWN"
    assert "infrastructure_error" in result
    assert json.loads((tmp_path / "reports/verification_review.json").read_text())["verdict"] == "UNKNOWN"


def test_scoped_pass_requires_zero_counts_and_review(tmp_path, monkeypatch):
    reports(tmp_path)
    monkeypatch.setattr(validation, "review", lambda *a, **kw: observed())
    result = pipeline.signoff_verdict(tmp_path)
    assert result["dual_clean"] is True and "infrastructure_error" not in result


@pytest.mark.parametrize("claimed,total,raw,verdict,expected", [
    (2, 2, 2, "PASS", True), (1, 1, 2, "PASS", False), (0, 0, 0, "PASS", False),
    (2, 2, 2, "UNKNOWN", False), (2, 2, 2, "STALE", False), (True, True, 1, "PASS", False)])
def test_final_acceptance_rechecks_review_and_measured_dataset_counts(tmp_path, monkeypatch, claimed, total, raw, verdict, expected):
    store = JobStore()
    jid = store.create_job("acceptance", {}, [{"name": "top"}])
    store.set_job(jid, "running")
    store.set_design(jid, "top", status="running", project=str(tmp_path), detail={"owner": OWNER})
    pipe = Pipeline(store, ingest=False, min_free_gb=0)
    monkeypatch.setattr(validation, "review", lambda *a, **kw: observed(verdict, raw))
    try:
        assert pipe._acceptance_gate(jid, "top", tmp_path, claimed, total) is expected
        record = store.design(jid, "top")
        assert record["status"] == ("running" if expected else "failed")
    finally:
        pipe.shutdown()


def test_package_rejects_new_pass_when_fresh_evidence_no_longer_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, "review", lambda *a, **kw: observed("STALE"))
    store = JobStore()
    jid = store.create_job("new acceptance", {}, [{"name": "top"}])
    store.set_design(jid, "top", status="passed", project=str(tmp_path), detail={"validation": observed()})
    with pytest.raises(ValueError, match="three-layer"):
        package.package_job(store, jid)
    assert not list((package.r2g_home() / "datasets").glob("*.tar.gz"))


def test_archive_binds_review_to_bytes_and_preserves_raw_evidence(tmp_path, monkeypatch):
    project = tmp_path / "project"
    files = {"dataset/graph.pt": b"graph", "lvs/6_lvs.lvsdb": b"native evidence",
             "backend/RUN_1/final/6_final.gds": b"checked layout"}
    for name, data in files.items():
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (project / "rtl").mkdir()
    (project / "constraints").mkdir()
    checked = observed()
    checked["input_snapshot"] = capture(project)
    checked["output_snapshot"] = capture_outputs(project, "graph")
    checked["evidence_files"] = [{"path": name, "sha256": hashlib.sha256(data).hexdigest()}
                                 for name, data in files.items() if not name.startswith("dataset/")]
    monkeypatch.setattr(validation, "review", lambda *a, **kw: checked)
    store = JobStore()
    jid = store.create_job("bound delivery", {}, [{"name": "top"}])
    store.set_design(jid, "top", status="passed", project=str(project), detail={"validation": observed()})
    archive = package.package_job(store, jid)
    with tarfile.open(archive) as tf:
        root = f"r2g_dataset_{jid}/top/"
        assert tf.extractfile(root + "lvs/6_lvs.lvsdb").read() == files["lvs/6_lvs.lvsdb"]
        assert tf.extractfile(root + "backend/RUN_1/final/6_final.gds").read() == b"checked layout"
        assert json.load(tf.extractfile(root + "reports/verification_review.json"))["verdict"] == "PASS"
    (project / "lvs/6_lvs.lvsdb").write_bytes(b"changed after review")
    with pytest.raises(ValueError, match="evidence changed"):
        package.package_job(store, jid)


def test_slim_keeps_final_signoff_inputs_and_raw_reports(tmp_path):
    final = tmp_path / "backend/RUN_1/final"
    final.mkdir(parents=True)
    (final / "6_final.gds").write_bytes(b"layout")
    (final / "6_final.v").write_bytes(b"netlist")
    (final / "6_final.odb").write_bytes(b"regenerable checkpoint")
    assert housekeeping.slim_passed(tmp_path) == len(b"regenerable checkpoint")
    assert (final / "6_final.gds").read_bytes() == b"layout"
    assert (final / "6_final.v").is_file() and not (final / "6_final.odb").exists()


def test_report_endpoint_recomputes_instead_of_trusting_saved_pass(tmp_path, monkeypatch):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/verification_review.json").write_text(json.dumps(observed()))
    monkeypatch.setattr(server, "_design_project", lambda *a: tmp_path)
    monkeypatch.setattr(validation, "review", lambda *a, **kw: observed("UNKNOWN"))
    assert server.reports("job", "top")["validation"]["verdict"] == "UNKNOWN"


def test_review_tool_is_read_only_and_reports_do_not_change(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    store = JobStore()
    jid = store.create_job("history", {}, [{"name": "top"}])
    store.set_design(jid, "top", project=str(project), status="passed")
    monkeypatch.setattr(tools.RT, "store", store)
    monkeypatch.setattr(validation, "review", lambda *a, **kw: observed("UNKNOWN"))
    assert "review_evidence" in tools.REVIEW_TOOLS
    result = tools.review_evidence(jid, "top")
    assert result["recorded_status"] == "passed" and result["verdict"] == "UNKNOWN"
    assert not list(project.iterdir())


def test_slim_retains_final_inputs_that_only_exist_in_results(tmp_path):
    results = tmp_path / "backend/RUN_1/results"
    results.mkdir(parents=True)
    for name in ("6_final.v", "6_final.sdc", "6_final.spef", "6_final.gds"):
        (results / name).write_text("original evidence")
    (results / "5_route.odb").write_bytes(b"regenerable")
    assert housekeeping.slim_passed(tmp_path) == len(b"regenerable")
    assert sorted(p.name for p in results.iterdir()) == ["6_final.gds", "6_final.sdc", "6_final.spef", "6_final.v"]


def test_package_includes_bound_results_layout_with_intermediates_retained(tmp_path, monkeypatch):
    project = tmp_path / "project"
    for name in ("rtl", "constraints", "dataset", "backend/RUN_1/results"):
        (project / name).mkdir(parents=True, exist_ok=True)
    (project / "dataset/graph.pt").write_bytes(b"graph")
    layout = project / "backend/RUN_1/results/6_final.gds"
    layout.write_bytes(b"actual checked bytes")
    native_sta = project / "backend/RUN_1/logs/6_report.json"
    native_sta.parent.mkdir()
    native_sta.write_bytes(b"native STA proof")
    checked = observed()
    checked["input_snapshot"] = capture(project)
    checked["output_snapshot"] = capture_outputs(project, "graph")
    checked["evidence_files"] = [{"path": "backend/RUN_1/results/6_final.gds",
                                  "sha256": hashlib.sha256(layout.read_bytes()).hexdigest()},
                                 {"path": "backend/RUN_1/logs/6_report.json",
                                  "sha256": hashlib.sha256(native_sta.read_bytes()).hexdigest()}]
    monkeypatch.setattr(validation, "review", lambda *a, **kw: checked)
    store = JobStore()
    jid = store.create_job("retained intermediates", {}, [{"name": "top"}])
    store.set_design(jid, "top", project=str(project), status="passed", detail={"validation": observed()})
    with tarfile.open(package.package_job(store, jid)) as tf:
        assert tf.extractfile(f"r2g_dataset_{jid}/top/backend/RUN_1/results/6_final.gds").read() == b"actual checked bytes"
        assert tf.extractfile(f"r2g_dataset_{jid}/top/backend/RUN_1/logs/6_report.json").read() == b"native STA proof"


def test_packaging_refusal_is_delivery_failure_and_never_claims_an_artifact(tmp_path, monkeypatch):
    store = JobStore()
    jid = store.create_job("delivery", {}, [{"name": "top"}])
    store.set_job(jid, "running")
    store.set_design(jid, "top", project=str(tmp_path), status="passed")
    def refused(*a, **kw):
        raise ValueError("evidence changed")
    monkeypatch.setattr(package, "package_job", refused)
    pipe = Pipeline(store, ingest=False, min_free_gb=0)
    try:
        assert pipe.finalize_if_done(jid)
        job = store.job(jid)
        assert job["status"] == "failed" and job["result"]["package_error"] == "evidence changed"
        assert "package" not in job["result"]
        event = store.events(jid)[-1]
        assert event["kind"] == "fail" and "交付失败" in event["message"]
        assert "通过并产出" not in event["message"]
    finally:
        pipe.shutdown()
