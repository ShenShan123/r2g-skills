"""Content-addressed state IDs (design doc 27.3 H1, test list 27.1).

Identical canonical content -> identical state_id (dedup, idempotent ingest);
any content change -> a different state_id. Timestamps never participate.
"""
from __future__ import annotations

from tehm.canonical.state import CanonicalState, source_digest
from tehm.canonical.verifier import toolchain_snapshot
from tehm.ids import stable_dumps


def _make_state(domain="flow.signoff", *, knob_value="0.10", commit="abc123"):
    src = source_digest({
        "repository_ref": commit,
        "config": {"PLACE_DENSITY_LB_ADDON": knob_value},
        "reports": {"drc": {"status": "violations"}},
    })
    return CanonicalState(
        domain=domain,
        design_id="demo",
        lineage_id="lineage_a",
        source_digest=src,
        context_graph_digest="ctx_same",
        verifier_snapshot=toolchain_snapshot({"iverilog": "1.0"}),
        artifact_manifest={"graph": {"digest": "sha256:aa"}},
        created_at="2026-07-31T00:00:00",  # volatile — must NOT affect the id
    )


def test_same_content_same_id():
    a = _make_state()
    b = _make_state()
    assert a.state_id == b.state_id
    assert a.state_id.startswith("state_")


def test_different_content_different_id():
    a = _make_state(knob_value="0.10")
    b = _make_state(knob_value="0.14")
    assert a.state_id != b.state_id


def test_timestamp_excluded_from_id():
    a = _make_state()
    b = _make_state()
    b.created_at = "2099-12-31T23:59:59"
    assert a.state_id == b.state_id


def test_domain_part_of_id():
    a = _make_state(domain="flow.signoff")
    b = _make_state(domain="rtl")
    assert a.state_id != b.state_id


def test_source_digest_deterministic_and_sensitive():
    import json
    content = {"config": {"A": "1"}, "reports": {"drc": {"status": "clean"}}}
    assert source_digest(content) == source_digest(json.loads(json.dumps(content)))
    mutated = json.loads(json.dumps(content))  # deep copy, then mutate
    mutated["reports"]["drc"]["status"] = "violations"
    assert source_digest(content) != source_digest(mutated)


def test_serialization_byte_stable():
    assert stable_dumps({"b": 2, "a": 1}) == stable_dumps({"a": 1, "b": 2})
    assert " " not in stable_dumps({"x": [1, 2]})
