"""Dataset results bind verifier execution, complete artifacts and delivered bytes."""
import json
from pathlib import Path
import sys

import pytest

from r2g_agent import artifact_snapshots as artifacts, validation
from r2g_agent.input_snapshots import capture as capture_inputs, reference, write_json
from r2g_agent.jobs import JobStore
from r2g_agent.pipeline import OWNER, Pipeline


def write(project, relative, value):
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)
    return path


def image_binding(project):
    core = [entry for entry in artifacts.capture(project, "image")["files"] if not entry["path"].startswith("images/png/")]
    return {"schema_version": 1, "scope": "core_files_unchanged_during_verifier", "before": core, "after": core}


def make_verified(tmp_path, kind):
    write(tmp_path, "rtl/top.v", "module top; endmodule")
    write(tmp_path, "constraints/top.sdc", "create_clock -period 1 clk")
    paths = ["dataset/graph.pt"] if kind == "graph" else [
        "images/features.npz", "images/labels.npz", "images/image_meta.json", "images/png/map.png"]
    for path in paths:
        write(tmp_path, path, "fixture artifact")
    if kind == "image":
        write(tmp_path, "reports/image_dataset.json", {"status": "ok", "verify": {"passed": 1, "total": 1}})
    before = artifacts.begin(tmp_path, kind)
    report = artifacts.verifier_path(kind)
    payload = {"results": [{"check": "fixture", "ok": True}], "passed": 1, "total": 1}
    if kind == "image":
        payload["output_binding"] = image_binding(tmp_path)
    write(tmp_path, report, payload)
    receipt = artifacts.finish(tmp_path, kind, before, "execution-1", 0)
    write_json(tmp_path / "reports/output_bindings.json", {kind: receipt})
    source = reference(capture_inputs(tmp_path))
    write_json(tmp_path / "reports/input_bindings.json", {receipt["stage"]: {
        "before": source, "after": source, "unchanged": True, "exit_code": 0, "execution_id": "execution-1"}})
    return tmp_path, kind, receipt, paths


@pytest.fixture(params=["graph", "image"])
def verified(tmp_path, request):
    return make_verified(tmp_path, request.param)


def checked(project):
    evidence = validation._Evidence(project)
    result = validation._dataset(evidence)
    return result, evidence.output_snapshot


def test_current_dataset_is_bound_to_verifier_and_exact_manifest(verified):
    project, kind, receipt, paths = verified
    result, snapshot = checked(project)
    assert result["verdict"] == "PASS" and snapshot == receipt["after"]
    assert {entry["path"] for entry in snapshot["files"]} == set(paths)
    assert (receipt["before"] is None) == (kind == "image")


@pytest.mark.parametrize("mutation", ["content", "add", "remove", "symlink", "verifier", "execution"])
def test_cached_pass_cannot_certify_changed_or_unbound_outputs(verified, mutation):
    project, kind, receipt, paths = verified
    if mutation == "content":
        (project / paths[0]).write_text("mutated artifact")
    elif mutation == "add":
        write(project, "dataset/extra.pt" if kind == "graph" else "images/png/new.png", "added")
    elif mutation == "remove":
        (project / paths[0]).unlink()
    elif mutation == "symlink":
        (project / paths[0]).unlink()
        (project / paths[0]).symlink_to(project / "rtl/top.v")
    elif mutation == "verifier":
        with (project / artifacts.verifier_path(kind)).open("a") as stream:
            stream.write("\n")
    else:
        receipt["execution_id"] = "another-execution"
        write_json(project / "reports/output_bindings.json", {kind: receipt})
    result, snapshot = checked(project)
    assert result["verdict"] in {"STALE", "UNKNOWN"} and snapshot is None


def test_legacy_verifier_without_artifact_receipt_is_unknown(verified):
    project, *_ = verified
    (project / "reports/output_bindings.json").unlink()
    assert checked(project)[0]["verdict"] == "UNKNOWN"


def test_nonzero_execution_cannot_bind_green_verifier(verified):
    project, kind, receipt, _ = verified
    receipt["exit_code"] = 1
    write_json(project / "reports/output_bindings.json", {kind: receipt})
    assert checked(project)[0]["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("mutation", ["missing", "different_core", "different_before"])
def test_image_native_binding_is_required_not_just_stage_exit(tmp_path, mutation):
    project, kind, receipt, _ = make_verified(tmp_path, "image")
    report_path = project / "images/verify.json"
    report = json.loads(report_path.read_text())
    if mutation == "missing":
        report.pop("output_binding")
    elif mutation == "different_core":
        report["output_binding"]["before"][0]["sha256"] = "changed"
        report["output_binding"]["after"][0]["sha256"] = "changed"
    else:
        report["output_binding"]["before"][0]["sha256"] = "changed"
    write(project, "images/verify.json", report)
    receipt = artifacts.finish(project, kind, receipt, "execution-1", 0)
    write_json(project / "reports/output_bindings.json", {kind: receipt})
    result, snapshot = checked(project)
    assert result["verdict"] == ("UNKNOWN" if mutation == "missing" else "STALE") and snapshot is None


def test_changed_during_graph_verifier_is_stale(tmp_path):
    write(tmp_path, "dataset/graph.pt", "original")
    before = artifacts.begin(tmp_path, "graph")
    write(tmp_path, "dataset/graph.pt", "changed")
    write(tmp_path, "reports/verify.json", {"results": [{"check": "fixture", "ok": True}]})
    receipt = artifacts.finish(tmp_path, "graph", before, "execution-1", 0)
    write_json(tmp_path / "reports/output_bindings.json", {"graph": receipt})
    write_json(tmp_path / "reports/input_bindings.json", {"verify": {"execution_id": "execution-1"}})
    e = validation._Evidence(tmp_path)
    e.document("reports/verify.json")
    assert validation._output_binding(e, "graph", "reports/verify.json") == (
        "STALE", "dataset_outputs_changed_during_verifier")


def test_graph_hash_is_streamed_and_bounded(tmp_path, monkeypatch):
    path = tmp_path / "dataset/graph.pt"
    path.parent.mkdir()
    path.write_bytes(b"x" * (artifacts.CHUNK_BYTES * 2 + 3))
    monkeypatch.setattr(Path, "read_bytes", lambda *args: pytest.fail("must stream graph bytes"))
    assert artifacts.capture(tmp_path, "graph")["files"][0]["size"] == artifacts.CHUNK_BYTES * 2 + 3
    monkeypatch.setattr(artifacts, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValueError, match="byte limit"):
        artifacts.capture(tmp_path, "graph")


def test_artifact_capture_refuses_directory_links_and_fifo(tmp_path):
    write(tmp_path, "dataset/graph.pt", "artifact")
    write(tmp_path, "outside/file", "private")
    (tmp_path / "dataset/link").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError, match="regular"):
        artifacts.capture(tmp_path, "graph")
    (tmp_path / "dataset/link").unlink()
    import os
    os.mkfifo(tmp_path / "dataset/fifo")
    with pytest.raises(ValueError, match="regular"):
        artifacts.capture(tmp_path, "graph")


@pytest.mark.parametrize("kind", ["graph", "image"])
@pytest.mark.parametrize("writes_report", [True, False])
def test_pipeline_records_actual_exit_and_does_not_reuse_old_verifier(tmp_path, kind, writes_report):
    write(tmp_path, "metadata.json", {})
    write(tmp_path, "rtl/top.v", "module top; endmodule")
    write(tmp_path, "constraints/top.sdc", "create_clock -period 1 clk")
    paths = ["dataset/graph.pt"] if kind == "graph" else [
        "images/features.npz", "images/labels.npz", "images/image_meta.json"]
    for path in paths:
        write(tmp_path, path, "artifact")
    report = artifacts.verifier_path(kind)
    write(tmp_path, report, {"results": [{"check": "stale", "ok": True}]})
    store = JobStore()
    jid = store.create_job("artifact binding", {"output": kind}, [{"name": "top"}])
    store.set_job(jid, "running")
    store.set_design(jid, "top", status="running", project=str(tmp_path), detail={"owner": OWNER})
    pipe = Pipeline(store, ingest=False, min_free_gb=0)
    payload = {"results": [{"check": "fresh", "ok": True}]}
    if kind == "image":
        payload["output_binding"] = image_binding(tmp_path)
    program = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])" if writes_report else "pass"
    try:
        assert pipe._sh(jid, "top", "verify" if kind == "graph" else "graph",
                        [sys.executable, "-c", program, str(tmp_path / report), json.dumps(payload)], tmp_path) == 0
    finally:
        pipe.shutdown()
    receipt = json.loads((tmp_path / "reports/output_bindings.json").read_text())[kind]
    assert receipt["exit_code"] == 0 and receipt["execution_id"]
    assert receipt["after"] == artifacts.capture(tmp_path, kind)
    if writes_report:
        assert receipt["verifier"]["path"] == report
        e = validation._Evidence(tmp_path)
        e.document(report)
        assert validation._output_binding(e, kind, report) == ("PASS", None)
    else:
        assert receipt["verifier"].get("unavailable") and not (tmp_path / report).exists()
