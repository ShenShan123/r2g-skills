"""Frozen-source guards for the isolated bootstrap diagnostic."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

from tehm import db


SPEC = importlib.util.spec_from_file_location(
    "knowledge_router_bootstrap_audit",
    Path(__file__).resolve().parents[1] / "scripts" / "audit_knowledge_router_bootstrap.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def frozen(tmp_path):
    source = tmp_path / "tehm.sqlite"
    conn = db.connect(source)
    db.ensure_schema(conn)
    db.checkpoint_and_close(conn)
    return source, hashlib.sha256(source.read_bytes()).hexdigest()


def test_bootstrap_rejects_wrong_digest_without_changing_source(frozen):
    source, digest = frozen
    with pytest.raises(ValueError, match="digest mismatch"):
        MODULE.audit(source, source_sha256="wrong", path_id="absent", campaign_id="live")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_bootstrap_missing_path_leaves_frozen_source_unchanged(frozen):
    source, digest = frozen
    before = sorted(p.name for p in source.parent.iterdir())
    with pytest.raises(KeyError, match="unknown causal path"):
        MODULE.audit(source, source_sha256=digest, path_id="absent", campaign_id="live")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert sorted(p.name for p in source.parent.iterdir()) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_bootstrap_rejects_sidecars(frozen, suffix):
    source, digest = frozen
    Path(str(source) + suffix).touch()
    with pytest.raises(ValueError, match="sidecar-free"):
        MODULE.audit(source, source_sha256=digest, path_id="absent", campaign_id="live")
