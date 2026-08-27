"""Content-addressed artifact integrity (design doc 19.8, honesty H4).

Artifacts live outside the DB as sha256 blobs; the DB keeps digests + refs only.
The H4 gate re-hashes every referenced blob and fails on mismatch or loss.
"""
from __future__ import annotations

from tehm import honesty
from tehm.canonical.capture import ExecutionRecord, capture


def test_put_get_roundtrip(tmp_tehm):
    _, store, _ = tmp_tehm
    manifest = store.put_json("report", {"violations": 7}, producer="test")
    assert manifest["digest"].startswith("sha256:")
    data = store.get(manifest)
    assert b"violations" in data
    assert store.verify(manifest)


def test_verify_detects_corruption(tmp_tehm):
    _, store, tmp = tmp_tehm
    manifest = store.put("report", b"original", producer="test")
    assert store.verify(manifest)
    # Corrupt the blob in place.
    rel = manifest["relative_path"]
    blob = tmp / "artifacts" / rel
    blob.write_bytes(b"tampered!")
    assert not store.verify(manifest)


def test_verify_detects_missing_blob(tmp_tehm):
    _, store, tmp = tmp_tehm
    manifest = store.put("report", b"gone", producer="test")
    rel = manifest["relative_path"]
    (tmp / "artifacts" / rel).unlink()
    assert not store.verify(manifest)


def test_captured_artifacts_verify_and_h4_green(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    ok, detail = honesty.h4_artifact_digest_integrity(conn, store)
    assert ok, detail


def test_h4_detects_corrupted_artifact(tmp_tehm, sample_record_dict):
    conn, store, tmp = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    # Corrupt every stored blob.
    for blob in (tmp / "artifacts" / "sha256").rglob("*"):
        if blob.is_file():
            blob.write_bytes(b"X" * blob.stat().st_size)
    ok, detail = honesty.h4_artifact_digest_integrity(conn, store)
    assert not ok
    assert "digest-mismatch" in detail


def test_unknown_artifact_kind_rejected(tmp_tehm):
    _, store, _ = tmp_tehm
    import pytest
    with pytest.raises(ValueError):
        store.put("mystery_binary", b"\x00\x01")
