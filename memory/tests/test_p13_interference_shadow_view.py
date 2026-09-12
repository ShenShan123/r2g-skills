"""Child/query compiler boundaries, not authority or hardware gain evidence."""
import json
from types import SimpleNamespace

import pytest

from scripts.audit_p13_interference_shadow_view import (
    InterferenceShadowViewError, _child, _query, audit_shadow_view,
)
from scripts.build_p13_interference_source_bound_plan import _digest
from test_knowledge import _claim


def test_child_is_new_shadow_identity_and_preserves_parent_causal_semantics():
    parent = _claim(version=3, status="validated")
    proposal = SimpleNamespace(proposal_digest="sha256:1234567890123456789012345",
                               negative_applicability=({"utility_contract_id": "zero-regression"},))
    child = _child(parent, proposal)
    assert child.knowledge_id != parent.knowledge_id
    assert child.version == 1 and child.status == "shadow"
    for name in ("mechanism_family", "compatibility_profile", "antecedent", "intervention",
                 "expected_outcome", "positive_applicability", "causal_path_ids",
                 "preserved_obligations", "evidence_level", "support_lineages"):
        assert getattr(child, name) == getattr(parent, name)
    assert all(entry in child.negative_applicability for entry in parent.negative_applicability)


def test_utility_adapter_is_immutable_and_does_not_introduce_an_outcome_label():
    frozen = {"query_plan": {"flow_config": {"CORE_UTILIZATION": "50"}},
              "dominant_dimensions": {}, "context_ref": None}
    pins = {"utility_contract_id": "zero-regression", "utility_contract_digest": "sha256:contract"}
    query = _query(frozen, pins)
    assert set(query.query_plan) == {"flow_config", *pins}
    assert set(frozen["query_plan"]) == {"flow_config"}


def test_adapter_rejects_reason_or_interference_label_payload():
    with pytest.raises(InterferenceShadowViewError, match="utility pins only"):
        _query({"query_plan": {}}, {"interference_signature": "harm"})


def test_existing_output_never_overwritten(tmp_path):
    output = tmp_path / "keep.json"
    output.write_text("keep")
    with pytest.raises(InterferenceShadowViewError, match="output must be new"):
        audit_shadow_view(output, output=output)
    assert output.read_text() == "keep"


def test_unbound_physical_scope_cannot_activate_shadow_view(tmp_path):
    plan = {"physical_utility_contract_bound": False}
    plan["report_digest"] = _digest(plan)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(InterferenceShadowViewError, match="physical contract binding"):
        audit_shadow_view(path, output=tmp_path / "audit.json")
