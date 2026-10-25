"""Delivery archives preserve actual bytes and refuse unsafe or oversized input."""
import hashlib
import json
import os
import tarfile

import pytest

from r2g_agent import package
from r2g_agent.jobs import JobStore


@pytest.fixture
def delivery(tmp_path):
    project = tmp_path / "project"
    files = {
        "dataset/graph.pt": b"graph payload",
        "rtl/top.v": b"module top(); endmodule\n",
        "rtl/include/defs.vh": b"`define WIDTH 8\n",
        "constraints/config.mk": b"export DESIGN_NAME = top\n",
        "constraints/constraint.sdc": b"create_clock -period 1 clk\n",
        "metadata.json": b'{"source":"original user source","top":"top"}',
        "agent_logs/flow.log": b"private full tool output excluded",
        ".llm_env": b"DO_NOT_INCLUDE",
        "backend/private.txt": b"DO_NOT_INCLUDE",
    }
    files.update({"reports/" + name: json.dumps({"status": "complete"}).encode()
                  for name in package.REPORTS if name.endswith(".json")})
    for name, content in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    store = JobStore()
    spec = {"platform": "nangate45", "output": "graph", "params": {"period_ns": 1}}
    jid = store.create_job("delivery", spec, [{"name": "top"}, {"name": "pending"}])
    store.set_design(jid, "top", project=str(project), status="passed",
                     detail={"signoff": {"drc": "clean", "lvs": "clean", "rcx": "complete"},
                             "verify": {"passed": 296, "total": 296}})
    return store, jid, project, files, spec


def _package(delivery, kind):
    store, jid, *_ = delivery
    return package.package_job(store, jid) if kind == "job" else package.package_design(store, jid, "top")


@pytest.mark.parametrize("kind", ["job", "design"])
def test_actual_archive_members_hashes_and_explicit_capture_scope(delivery, kind):
    store, jid, project, files, spec = delivery
    out = _package(delivery, kind)
    root = f"r2g_dataset_{jid}/top" if kind == "job" else "r2g_top"
    with tarfile.open(out) as tar:
        assert all(member.isfile() for member in tar)
        names = tar.getnames()
        assert not any("pending" in name or "DO_NOT_INCLUDE" in name for name in names)
        assert not any("agent_logs/" in name or ".llm_env" in name or "backend/" in name for name in names)
        manifest = json.load(tar.extractfile(root + "/delivery_manifest.json"))
        assert manifest["job_id"] == jid and manifest["design"] == "top"
        assert manifest["spec"] == spec
        assert manifest["capture"] == {
            "authority": "packaging_time_inventory", "pre_execution_source_verification": "unverified",
            "cross_file_snapshot": "unverified"}
        assert manifest["source_snapshot_id"] is None
        assert "evidence_binding_to_packaged_inputs" in manifest["unverified"]
        assert manifest["recorded_checks"]["dataset"] == {"passed": 296, "total": 296}
        assert manifest["log_references"] == [
            {"path": "agent_logs/flow.log", "included": False, "location": "job_workspace"}]
        inventory = {entry["path"]: entry for entry in manifest["members"]}
        actual = {name[len(root) + 1:] for name in names if name.startswith(root + "/")}
        assert actual == set(inventory) | {"delivery_manifest.json"}
        for relative, entry in inventory.items():
            data = tar.extractfile(root + "/" + relative).read()
            assert len(data) == entry["size"]
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            if relative in files:
                assert data == files[relative]
        assert {"reports/rcx.json", "reports/route.json", "reports/timing_check.json",
                "reports/signoff_gate.json", "rtl/top.v", "rtl/include/defs.vh",
                "constraints/config.mk", "constraints/constraint.sdc"} <= set(inventory)
        inputs = [entry for entry in manifest["members"] if entry["path"].startswith(("rtl/", "constraints/"))]
        assert manifest["packaging_input_manifest_sha256"] == hashlib.sha256(
            json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert (project / "metadata.json").read_bytes() == files["metadata.json"]


@pytest.mark.parametrize("member", ["dataset/outside.pt", "rtl/outside.v", "reports/drc.json", "metadata.json"])
def test_symlinks_to_outside_files_are_rejected(delivery, tmp_path, member):
    project = delivery[2]
    outside = tmp_path / "outside-secret"
    outside.write_text("do not archive")
    target = project / member
    target.unlink(missing_ok=True)
    target.symlink_to(outside)
    with pytest.raises((ValueError, OSError)):
        _package(delivery, "job")
    assert not list((package.r2g_home() / "datasets").glob("*.tar.gz"))


def test_symlink_directory_is_rejected(delivery, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("excluded")
    (delivery[2] / "rtl/link").symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        _package(delivery, "design")


def test_non_regular_input_is_rejected_without_opening_fifo(delivery):
    os.mkfifo(delivery[2] / "dataset/fifo")
    with pytest.raises(ValueError, match="regular files"):
        _package(delivery, "job")


@pytest.mark.parametrize("bad", ["../outside", "/absolute", "a/b", "a\\b", "..", "bad\nname"])
def test_archive_identity_cannot_escape_output_directory(delivery, bad):
    with pytest.raises(ValueError, match="unsafe archive path"):
        package.package_design(delivery[0], delivery[1], bad)
    with pytest.raises(ValueError, match="unsafe archive path"):
        package.package_job(delivery[0], bad)


def test_project_path_cannot_escape_via_parent_or_symlink(delivery, tmp_path):
    store, jid, project, *_ = delivery
    actual_job = store.job(jid)
    for path in (project / ".." / "project", tmp_path / "linked_project"):
        if path.name == "linked_project":
            path.symlink_to(project, target_is_directory=True)
        actual_job["designs"][0]["project"] = str(path)
        class Store:
            def job(self, _jid):
                return actual_job
        with pytest.raises((ValueError, OSError)):
            package.package_job(Store(), jid)


@pytest.mark.parametrize("kind", ["job", "design"])
@pytest.mark.parametrize("failure", ["file_size", "archive_size", "member_count", "unsafe_file", "replace"])
def test_failed_publication_preserves_previous_archive(delivery, monkeypatch, kind, failure):
    out = _package(delivery, kind)
    original = out.read_bytes()
    if failure == "file_size":
        monkeypatch.setattr(package, "MAX_FILE_BYTES", 5)
    elif failure == "archive_size":
        monkeypatch.setattr(package, "MAX_ARCHIVE_BYTES", 20)
    elif failure == "member_count":
        monkeypatch.setattr(package, "MAX_MEMBERS", 2)
    elif failure == "unsafe_file":
        (delivery[2] / "dataset/link").symlink_to(delivery[2] / "metadata.json")
    else:
        def fail_replace(*args):
            raise OSError("simulated publication failure")
        monkeypatch.setattr(package.os, "replace", fail_replace)
    with pytest.raises((ValueError, OSError)):
        _package(delivery, kind)
    assert out.read_bytes() == original
    assert list(out.parent.glob("*.tmp")) == []


def test_partial_archive_does_not_replace_final(delivery):
    final = _package(delivery, "job")
    original = final.read_bytes()
    part = package.package_job(delivery[0], delivery[1], partial=True)
    assert part != final and part.name.endswith("_partial.tar.gz")
    assert final.read_bytes() == original
    with tarfile.open(part) as tar:
        index = json.load(tar.extractfile(f"r2g_dataset_{delivery[1]}/index.json"))
        assert index["partial"] is True


def test_image_payload_has_same_inventory_and_input_contract(delivery):
    store, jid, project, *_ = delivery
    for relative in ("images/features.npz", "images/labels.npz", "images/image_meta.json",
                     "images/verify.json", "images/png/map.png"):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image data")
    image_id = store.create_job("images", {"output": "image"}, [{"name": "top"}])
    store.set_design(image_id, "top", project=str(project), status="passed")
    with tarfile.open(package.package_design(store, image_id, "top")) as tar:
        manifest = json.load(tar.extractfile("r2g_top/delivery_manifest.json"))
        names = {member["path"] for member in manifest["members"]}
        assert "images/png/map.png" in names and "rtl/top.v" in names
        assert not any(name.startswith("dataset/") for name in names)


@pytest.mark.parametrize("relative", ["dataset/graph.pt", "rtl/top.v"])
def test_disappearing_enumerated_file_cannot_be_silently_omitted(delivery, monkeypatch, relative):
    original = package._Archive.file

    def remove_then_package(self, base, member, root, **kwargs):
        if member == relative:
            (delivery[2] / relative).unlink()
        return original(self, base, member, root, **kwargs)

    monkeypatch.setattr(package._Archive, "file", remove_then_package)
    with pytest.raises(FileNotFoundError):
        _package(delivery, "job")


def test_traversal_limit_counts_nested_empty_directories(delivery, monkeypatch):
    path = delivery[2] / "dataset"
    for _ in range(12):
        path /= "child"
        path.mkdir()
    monkeypatch.setattr(package, "MAX_MEMBERS", 10)
    with pytest.raises(ValueError, match="traversal entry count"):
        _package(delivery, "job")


def test_missing_reports_remain_explicitly_unverified(delivery):
    (delivery[2] / "reports/rcx.json").unlink()
    with tarfile.open(_package(delivery, "design")) as tar:
        manifest = json.load(tar.extractfile("r2g_top/delivery_manifest.json"))
        assert manifest["missing_reports"] == ["reports/rcx.json"]
        assert "required_reports_missing" in manifest["unverified"]


def test_file_mutation_during_capture_aborts_publication(delivery, monkeypatch):
    original = package._HashingReader.read
    changed = False

    def mutate_after_read(reader, size):
        nonlocal changed
        data = original(reader, size)
        if not changed:
            with (delivery[2] / "dataset/graph.pt").open("ab") as stream:
                stream.write(b"changed during capture")
            changed = True
        return data

    monkeypatch.setattr(package._HashingReader, "read", mutate_after_read)
    with pytest.raises(ValueError, match="changed while packaging"):
        _package(delivery, "job")
