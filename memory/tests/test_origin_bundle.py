"""Pinned lexical origin aliases must never fall back or grant authority."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tehm.origin_bundle import (
    OriginBundle, OriginBundleError, OriginReadPlan, READ_PLAN_SCHEMA, SCHEMA,
)


def _sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _bundle(tmp_path, data=b'{"raw":1}', original="/original/project/raw.json"):
    root = tmp_path / "bundle"
    root.mkdir()
    value = _sha(data)
    hexed = value.removeprefix("sha256:")
    relative = f"sha256/{hexed[:2]}/{hexed}"
    blob = root / relative
    blob.parent.mkdir(parents=True)
    blob.write_bytes(data)
    manifest = {"schema": SCHEMA, "aliases": [{"original_path": original, "sha256": value}],
                "blobs": {value: {"relative_path": relative, "size": len(data)}}}
    pin = _save(root, manifest)
    return root, manifest, pin, value, blob


def _save(root, manifest):
    data = json.dumps(manifest, sort_keys=True).encode()
    (root / "manifest.json").write_bytes(data)
    return _sha(data)


def _plan_digest(bindings):
    rows = sorted(bindings, key=lambda row: (row["original_path"], row["sha256"]))
    payload = {"schema": READ_PLAN_SCHEMA, "bindings": rows}
    return _sha(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode())


def test_exact_origin_bytes_json_and_bounded_receipt(tmp_path):
    root, manifest, pin, value, _ = _bundle(tmp_path)
    bundle = OriginBundle(root, expected_manifest_sha256=pin)
    saved = copy.deepcopy(manifest)
    assert bundle.read_json("/original/project/raw.json", expected_sha256=value) == {"raw": 1}
    assert bundle.read_bytes("/original/project/raw.json", expected_sha256=value[7:]) == b'{"raw":1}'
    receipt = bundle.verify()
    assert receipt["alias_count"] == receipt["blob_count"] == 1
    assert not receipt["production_authority"] and not receipt["canonical_replayed"]
    assert not receipt["toolchain_probed"]
    assert manifest == saved


@pytest.mark.parametrize("value", [None, "", "sha256:a", "a" * 63, "a" * 65, "A" * 64, "g" * 64, 1])
def test_independent_manifest_pin_required(tmp_path, value):
    root, _, _, _, _ = _bundle(tmp_path)
    with pytest.raises(OriginBundleError):
        OriginBundle(root, expected_manifest_sha256=value)


def test_self_consistent_manifest_is_not_an_independent_pin(tmp_path):
    root, manifest, pin, _, _ = _bundle(tmp_path)
    manifest["aliases"][0]["original_path"] = "/substituted/raw.json"
    _save(root, manifest)
    with pytest.raises(OriginBundleError, match="manifest pin"):
        OriginBundle(root, expected_manifest_sha256=pin)


@pytest.mark.parametrize("path", ["relative", "/", "//a", "/a//b", "/a/./b", "/a/../b", "/a/", "/a\x00b", None])
def test_ambiguous_lexical_aliases_rejected(tmp_path, path):
    root, manifest, _, _, _ = _bundle(tmp_path)
    manifest["aliases"][0]["original_path"] = path
    with pytest.raises(OriginBundleError):
        OriginBundle(root, expected_manifest_sha256=_save(root, manifest))


@pytest.mark.parametrize("tamper", ["schema", "extra_field", "duplicate_alias", "missing_blob", "unreferenced",
    "traversal", "absolute_blob", "alternate_blob", "negative_size", "bool_size", "extra_blob_field", "bare_blob_sha"])
def test_recomputed_manifest_cannot_weaken_schema(tmp_path, tamper):
    root, manifest, _, value, _ = _bundle(tmp_path)
    row = manifest["blobs"][value]
    if tamper == "schema":
        manifest["schema"] = "future-v2"
    elif tamper == "extra_field":
        manifest["valid"] = True
    elif tamper == "duplicate_alias":
        manifest["aliases"].append(copy.deepcopy(manifest["aliases"][0]))
    elif tamper == "missing_blob":
        manifest["blobs"] = {}
    elif tamper == "unreferenced":
        manifest["aliases"] = []
    elif tamper == "traversal":
        row["relative_path"] = "../raw.json"
    elif tamper == "absolute_blob":
        row["relative_path"] = "/outside/raw.json"
    elif tamper == "alternate_blob":
        row["relative_path"] = "sha256/xx/" + value[7:]
    elif tamper == "negative_size":
        row["size"] = -1
    elif tamper == "bool_size":
        row["size"] = True
    elif tamper == "extra_blob_field":
        row["valid"] = True
    else:
        manifest["blobs"][value[7:]] = manifest["blobs"].pop(value)
    with pytest.raises(OriginBundleError):
        OriginBundle(root, expected_manifest_sha256=_save(root, manifest))


@pytest.mark.parametrize("tamper", ["corrupt", "truncate", "grow", "delete", "file_alias", "directory_alias", "fifo", "extra_file", "extra_link"])
def test_actual_payload_not_just_manifest_replayed(tmp_path, tamper):
    root, _, pin, _, blob = _bundle(tmp_path)
    if tamper == "corrupt":
        blob.write_bytes(b"X" * blob.stat().st_size)
    elif tamper == "truncate":
        blob.write_bytes(b"")
    elif tamper == "grow":
        blob.write_bytes(b"X" * 100)
    elif tamper == "delete":
        blob.unlink()
    elif tamper == "file_alias":
        external = tmp_path / "external"
        blob.rename(external)
        blob.symlink_to(external)
    elif tamper == "directory_alias":
        external = tmp_path / "external"
        blob.parent.rename(external)
        blob.parent.symlink_to(external, target_is_directory=True)
    elif tamper == "fifo":
        import os
        blob.unlink()
        os.mkfifo(blob)
    elif tamper == "extra_file":
        (root / "unexpected").write_bytes(b"extra")
    else:
        (root / "unexpected").symlink_to(tmp_path / "absent")
    with pytest.raises(OriginBundleError):
        OriginBundle(root, expected_manifest_sha256=pin)


def test_each_read_rechecks_blob_and_manifest_after_construction(tmp_path):
    root, manifest, pin, value, blob = _bundle(tmp_path)
    bundle = OriginBundle(root, expected_manifest_sha256=pin)
    blob.write_bytes(b"X" * blob.stat().st_size)
    with pytest.raises(OriginBundleError, match="blob pin"):
        bundle.read_bytes("/original/project/raw.json", expected_sha256=value)
    blob.write_bytes(b'{"raw":1}')
    manifest["aliases"][0]["original_path"] = "/new/raw.json"
    _save(root, manifest)
    with pytest.raises(OriginBundleError, match="manifest pin"):
        bundle.read_bytes("/original/project/raw.json", expected_sha256=value)


def test_no_live_fallback_even_when_original_exists(tmp_path):
    live = tmp_path / "live.json"
    live.write_bytes(b'{"raw":1}')
    root, _, pin, value, blob = _bundle(tmp_path, original=str(live))
    bundle = OriginBundle(root, expected_manifest_sha256=pin)
    blob.unlink()
    with pytest.raises(OriginBundleError):
        bundle.read_bytes(str(live), expected_sha256=value)
    assert live.read_bytes() == b'{"raw":1}'


def test_missing_alias_and_wrong_independent_file_pin_rejected(tmp_path):
    root, _, pin, value, _ = _bundle(tmp_path)
    bundle = OriginBundle(root, expected_manifest_sha256=pin)
    for original, expected in [("/missing/raw.json", value), ("/original/project/raw.json", "0" * 64)]:
        with pytest.raises(OriginBundleError):
            bundle.read_bytes(original, expected_sha256=expected)


@pytest.mark.parametrize("data", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'not-json', b'\xff'])
def test_pinned_bytes_still_require_unambiguous_json(tmp_path, data):
    root, _, pin, value, _ = _bundle(tmp_path, data=data)
    bundle = OriginBundle(root, expected_manifest_sha256=pin)
    with pytest.raises(OriginBundleError):
        bundle.read_json("/original/project/raw.json", expected_sha256=value)


def test_duplicate_manifest_keys_rejected_with_correct_binary_pin(tmp_path):
    root, _, _, _, _ = _bundle(tmp_path)
    data = b'{"schema":"tehm-origin-bundle-v1","schema":"tehm-origin-bundle-v1","aliases":[],"blobs":{}}'
    (root / "manifest.json").write_bytes(data)
    with pytest.raises(OriginBundleError):
        OriginBundle(root, expected_manifest_sha256=_sha(data))


def test_relocation_preserves_original_alias_without_reading_original(tmp_path):
    root, _, pin, value, _ = _bundle(tmp_path)
    relocated = tmp_path / "relocated"
    root.rename(relocated)
    bundle = OriginBundle(relocated, expected_manifest_sha256=pin)
    assert bundle.read_json("/original/project/raw.json", expected_sha256=value) == {"raw": 1}


def test_two_aliases_can_share_one_blob(tmp_path):
    root, manifest, _, value, _ = _bundle(tmp_path)
    manifest["aliases"].append({"original_path": "/other/raw.json", "sha256": value})
    bundle = OriginBundle(root, expected_manifest_sha256=_save(root, manifest))
    assert bundle.verify()["alias_count"] == 2
    assert bundle.read_bytes("/other/raw.json", expected_sha256=value) == b'{"raw":1}'


@pytest.mark.parametrize("location", ["root", "manifest", "shard", "blob"])
def test_retargeting_after_construction_is_rejected_on_read(tmp_path, location):
    root, _, pin, value, blob = _bundle(tmp_path)
    bundle = OriginBundle(root, expected_manifest_sha256=pin)
    target = {"root": root, "manifest": root / "manifest.json", "shard": blob.parent,
              "blob": blob}[location]
    external = tmp_path / "external"
    is_directory = target.is_dir()
    target.rename(external)
    target.symlink_to(external, target_is_directory=is_directory)
    with pytest.raises(OriginBundleError):
        bundle.read_bytes("/original/project/raw.json", expected_sha256=value)


def test_zero_byte_artifact_has_a_real_pin(tmp_path):
    root, _, pin, value, _ = _bundle(tmp_path, data=b"")
    bundle = OriginBundle(root, expected_manifest_sha256=pin)
    assert bundle.read_bytes("/original/project/raw.json", expected_sha256=value) == b""


def test_full_verify_detects_extra_file_created_after_construction(tmp_path):
    root, _, pin, _, _ = _bundle(tmp_path)
    bundle = OriginBundle(root, expected_manifest_sha256=pin)
    (root / "extra").write_bytes(b"extra")
    with pytest.raises(OriginBundleError, match="inventory"):
        bundle.verify()


def test_origin_read_plan_preserves_logical_identity_and_reads_bundle_only(tmp_path):
    live = tmp_path / "live.json"
    live.write_bytes(b'{"source":"live"}')
    archived = b'{"source":"archive"}'
    root, _, manifest_pin, value, _ = _bundle(
        tmp_path, data=archived, original=str(live),
    )
    bindings = [{"original_path": str(live), "sha256": value}]
    plan = OriginReadPlan(
        OriginBundle(root, expected_manifest_sha256=manifest_pin), bindings,
        expected_plan_digest=_plan_digest(bindings),
    )
    assert plan.read_bytes(str(live)) == archived
    assert plan.read_text(str(live)) == archived.decode()
    assert plan.read_json(str(live)) == {"source": "archive"}
    assert live.read_bytes() == b'{"source":"live"}'
    receipt = plan.verify()
    assert receipt["logical_paths_preserved"] is True
    assert receipt["live_filesystem_fallback"] is False
    assert receipt["binding_count"] == 1
    assert not receipt["canonical_replayed"] and not receipt["toolchain_probed"]
    assert not receipt["production_authority"]


def test_origin_read_plan_is_order_canonical_but_requires_independent_pin(tmp_path):
    root, manifest, _, first, _ = _bundle(tmp_path)
    second_data = b"second"
    second = _sha(second_data)
    second_hex = second.removeprefix("sha256:")
    second_relative = f"sha256/{second_hex[:2]}/{second_hex}"
    blob = root / second_relative
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(second_data)
    manifest["aliases"].append({"original_path": "/original/second", "sha256": second})
    manifest["blobs"][second] = {"relative_path": second_relative, "size": len(second_data)}
    manifest_pin = _save(root, manifest)
    bindings = [
        {"original_path": "/original/second", "sha256": second},
        {"original_path": "/original/project/raw.json", "sha256": first},
    ]
    plan_pin = _plan_digest(list(reversed(bindings)))
    plan = OriginReadPlan(
        OriginBundle(root, expected_manifest_sha256=manifest_pin), bindings,
        expected_plan_digest=plan_pin,
    )
    assert plan.plan_digest == plan_pin
    with pytest.raises(OriginBundleError, match="plan pin"):
        OriginReadPlan(
            plan.bundle, bindings,
            expected_plan_digest="sha256:" + "0" * 64,
        )


@pytest.mark.parametrize("tamper", ["missing", "duplicate", "extra", "bare", "bad_path"])
def test_origin_read_plan_rejects_unbound_or_ambiguous_bindings(tmp_path, tamper):
    root, _, manifest_pin, value, _ = _bundle(tmp_path)
    binding = {"original_path": "/original/project/raw.json", "sha256": value}
    bindings = [binding]
    if tamper == "missing":
        bindings = [{"original_path": "/missing", "sha256": value}]
    elif tamper == "duplicate":
        bindings = [binding, dict(binding)]
    elif tamper == "extra":
        bindings = [{**binding, "authority": True}]
    elif tamper == "bare":
        bindings = [{**binding, "sha256": value.removeprefix("sha256:")}]
    else:
        bindings = [{**binding, "original_path": "/original/../raw.json"}]
    bundle = OriginBundle(root, expected_manifest_sha256=manifest_pin)
    with pytest.raises(OriginBundleError):
        OriginReadPlan(bundle, bindings, expected_plan_digest=_plan_digest(bindings))


def test_origin_read_plan_rechecks_payload_and_strict_decoders(tmp_path):
    root, _, manifest_pin, value, blob = _bundle(tmp_path, data=b'not-json')
    bindings = [{"original_path": "/original/project/raw.json", "sha256": value}]
    plan = OriginReadPlan(
        OriginBundle(root, expected_manifest_sha256=manifest_pin), bindings,
        expected_plan_digest=_plan_digest(bindings),
    )
    with pytest.raises(OriginBundleError, match="JSON"):
        plan.read_json("/original/project/raw.json")
    blob.write_bytes(b"X" * blob.stat().st_size)
    with pytest.raises(OriginBundleError, match="blob pin"):
        plan.read_bytes("/original/project/raw.json")

    bad_parent = tmp_path / "bad"
    bad_parent.mkdir()
    bad_root, _, bad_manifest_pin, bad_value, _ = _bundle(
        bad_parent, data=b"\xff", original="/original/non-utf8",
    )
    bad_bindings = [{"original_path": "/original/non-utf8", "sha256": bad_value}]
    bad_plan = OriginReadPlan(
        OriginBundle(bad_root, expected_manifest_sha256=bad_manifest_pin), bad_bindings,
        expected_plan_digest=_plan_digest(bad_bindings),
    )
    with pytest.raises(OriginBundleError, match="UTF-8"):
        bad_plan.read_text("/original/non-utf8")
