"""Reviewed ordinary deliveries bind the actual archived input bytes."""
import copy
import json
import os
import tarfile

import pytest

from r2g_agent import package, validation
from r2g_agent.input_snapshots import capture
from r2g_agent.artifact_snapshots import capture as capture_outputs
from r2g_agent.jobs import JobStore


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    project = tmp_path / "project"
    files = {"rtl/top.v": "module top; endmodule\n", "rtl/include/defs.inc": "`define N 1\n",
             "constraints/constraint.sdc": "create_clock -period 1 clk\n", "dataset/graph.pt": "fixture"}
    for name, data in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
    checked = {"schema_version": 1, "verdict": "PASS", "policy_sha256": "a" * 64,
               "input_snapshot": capture(project), "output_snapshot": capture_outputs(project, "graph"),
               "evidence_files": [], "checks": {
                   **{kind: {"verdict": "PASS"} for kind in validation.REQUIRED},
                   "dataset": {"verdict": "PASS"}}}
    monkeypatch.setattr(validation, "review", lambda *a, **kw: copy.deepcopy(checked))
    store = JobStore()
    jid = store.create_job("ordinary delivery", {}, [{"name": "top"}])
    store.set_design(jid, "top", status="passed", project=str(project),
                     detail={"validation": validation.validation_summary({"validation": checked})})
    return store, jid, project, checked


def publish(delivery, kind="job"):
    store, jid, *_ = delivery
    return package.package_job(store, jid) if kind == "job" else package.package_design(store, jid, "top")


@pytest.mark.parametrize("kind", ["job", "design"])
def test_ordinary_delivery_manifest_binds_verified_inputs(delivery, kind):
    store, jid, project, checked = delivery
    assert "repair_validation" not in store.design(jid, "top")["detail"]
    with tarfile.open(publish(delivery, kind)) as archive:
        root = f"r2g_dataset_{jid}/top/" if kind == "job" else "r2g_top/"
        manifest = json.load(archive.extractfile(root + "delivery_manifest.json"))
        assert manifest["source_snapshot_id"] == checked["input_snapshot"]["sha256"]
        assert manifest["acceptance_policy_sha256"] == checked["policy_sha256"]
        assert manifest["output_snapshot_id"] == checked["output_snapshot"]["sha256"]
        assert manifest["capture"]["authority"] == "bound_execution_inputs"
        assert "evidence_binding_to_packaged_inputs" not in manifest["unverified"]
        assert "reviewable_change_diff" in manifest["unverified"]
        assert archive.extractfile(root + "rtl/top.v").read() == (project / "rtl/top.v").read_bytes()


@pytest.mark.parametrize("kind", ["job", "design"])
@pytest.mark.parametrize("relative", ["rtl/top.v", "rtl/include/defs.inc", "constraints/constraint.sdc"])
def test_change_after_review_before_archive_read_preserves_previous_package(delivery, monkeypatch, kind, relative):
    out = publish(delivery, kind)
    original_archive = out.read_bytes()
    original_file = package._Archive.file
    changed = False

    def replace_before_read(self, base, name, root, **kwargs):
        nonlocal changed
        if name == relative and not changed:
            path = delivery[2] / relative
            before = path.stat()
            content = path.read_bytes()
            path.write_bytes(b"X" + content[1:])
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            changed = True
        return original_file(self, base, name, root, **kwargs)

    monkeypatch.setattr(package._Archive, "file", replace_before_read)
    with pytest.raises(ValueError, match="delivery inputs changed"):
        publish(delivery, kind)
    assert changed and out.read_bytes() == original_archive
    assert list(out.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("mutation", ["add", "remove"])
def test_review_to_enumeration_membership_change_is_rejected(delivery, monkeypatch, mutation):
    def review_then_change(*args, **kwargs):
        if mutation == "add":
            (delivery[2] / "rtl/new.inc").write_text("new source")
        else:
            (delivery[2] / "constraints/constraint.sdc").unlink()
        return copy.deepcopy(delivery[3])

    monkeypatch.setattr(validation, "review", review_then_change)
    with pytest.raises(ValueError, match="delivery inputs changed"):
        publish(delivery)
    assert not list((package.r2g_home() / "datasets").glob("*.tar.gz"))


def test_claimed_pass_without_verified_input_snapshot_cannot_publish(delivery):
    delivery[3].pop("input_snapshot")
    with pytest.raises(ValueError, match="verified execution snapshot"):
        publish(delivery)


def test_snapshot_content_cannot_disagree_with_its_digest(delivery):
    delivery[3]["input_snapshot"]["rtl"]["files"][0]["size"] += 1
    with pytest.raises(ValueError, match="delivery inputs changed"):
        publish(delivery)


@pytest.mark.parametrize("mutation", ["content", "add", "missing_snapshot"])
def test_output_change_after_review_cannot_publish(delivery, monkeypatch, mutation):
    out = publish(delivery)
    old = out.read_bytes()
    if mutation == "missing_snapshot":
        delivery[3].pop("output_snapshot")
    else:
        original = package._Archive.file

        def change_before_read(self, base, relative, root, **kwargs):
            if relative == "dataset/graph.pt":
                (delivery[2] / relative).write_bytes(b"changed graph")
            return original(self, base, relative, root, **kwargs)

        if mutation == "content":
            monkeypatch.setattr(package._Archive, "file", change_before_read)
        else:
            (delivery[2] / "dataset/extra.pt").write_bytes(b"added graph")
    with pytest.raises(ValueError, match="delivery outputs changed"):
        publish(delivery)
    assert out.read_bytes() == old and list(out.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("relative", ["images/features.npz", "images/labels.npz", "images/image_meta.json", "images/png/map.png"])
def test_image_delivery_binds_each_packaged_artifact(delivery, monkeypatch, relative):
    store, _, project, checked = delivery
    for path in ("images/features.npz", "images/labels.npz", "images/image_meta.json", "images/png/map.png"):
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"image fixture")
    checked["output_snapshot"] = capture_outputs(project, "image")
    jid = store.create_job("image delivery", {"output": "image"}, [{"name": "top"}])
    store.set_design(jid, "top", status="passed", project=str(project), detail={"validation": checked})
    image_delivery = store, jid, project, checked
    out = publish(image_delivery)
    before = out.read_bytes()
    original = package._Archive.file

    def change_before_read(self, base, path, root, **kwargs):
        if path == relative:
            (project / relative).write_bytes(b"changed image fixture")
        return original(self, base, path, root, **kwargs)

    monkeypatch.setattr(package._Archive, "file", change_before_read)
    with pytest.raises(ValueError, match="delivery outputs changed"):
        publish(image_delivery)
    assert out.read_bytes() == before
