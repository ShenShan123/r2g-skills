"""Policy compiler fail-closed boundaries, not held-out execution evidence."""
import sqlite3
from types import SimpleNamespace

import pytest

from scripts.audit_p13_interference_shadow_view import (
    InterferenceShadowViewError, _activate_evaluation_child,
)
from scripts.build_p13_interference_policy_views import (
    InterferencePolicyViewError, _audit_partition, _policy_candidates,
    _runtime_code_binding, _self_digest, build_policy_views,
)
from scripts.build_p13_interference_source_bound_plan import _digest


def _partition_case(split="held_out"):
    case = {"case_id": "audit", "dataset_split": split, "role": split, "learner_eligible": False}
    authority = {"learner_partition": {"learner_eligible": False, "cases": {
        "audit": {key: case[key] for key in ("dataset_split", "role", "learner_eligible")}}}}
    return authority, case


@pytest.mark.parametrize("split", ["validation", "held_out", "calibration"])
def test_audit_partition_preserves_prospective_nonlearner_role(split):
    authority, case = _partition_case(split)
    assert _audit_partition(authority, [case]) == authority["learner_partition"]
    assert case["role"] == split


@pytest.mark.parametrize("mutation", ["missing", "training", "eligible", "conflicting_case", "coverage"])
def test_no_posthoc_role_inference_or_learner_grant(mutation):
    authority, case = _partition_case()
    if mutation == "missing":
        authority.clear()
    elif mutation == "training":
        authority, case = _partition_case("training")
    elif mutation == "eligible":
        authority["learner_partition"]["learner_eligible"] = True
    elif mutation == "conflicting_case":
        case["role"] = "training"
    else:
        case["case_id"] = "foreign"
    with pytest.raises(InterferencePolicyViewError):
        _audit_partition(authority, [case])


@pytest.mark.parametrize("decision", ["INAPPLICABLE", "NO_SKILL", "ABSTAIN"])
def test_actual_nonmemory_route_emits_gated_fallback_but_keeps_forced_memory(decision):
    historical = object()
    arms = _policy_candidates(SimpleNamespace(decision=decision), None, historical)
    assert arms == {"NO_MEMORY": None, "ALWAYS_MEMORY": historical,
                    "APPLICABILITY_GATED": None, "CAUSAL_NO_SKILL": None}


@pytest.mark.parametrize("decision", ["RISK", "INSUFFICIENT_EVIDENCE", "UNKNOWN"])
def test_reason_or_unknown_label_is_not_a_top_level_route(decision):
    with pytest.raises(InterferencePolicyViewError, match="unknown top-level"):
        _policy_candidates(SimpleNamespace(decision=decision), None, object())


@pytest.mark.parametrize("decision,current", [("CONSIDER", None), ("APPLY", None), ("INAPPLICABLE", object())])
def test_candidate_null_cannot_contradict_actual_router(decision, current):
    with pytest.raises(InterferencePolicyViewError, match="contradiction"):
        _policy_candidates(SimpleNamespace(decision=decision), current, object())


def test_actual_selected_candidate_is_preserved_separately_from_forced_historical():
    current, historical = object(), object()
    arms = _policy_candidates(SimpleNamespace(decision="CONSIDER"), current, historical)
    assert arms["ALWAYS_MEMORY"] is historical
    assert arms["APPLICABILITY_GATED"] is arms["CAUSAL_NO_SKILL"] is current


def test_forced_memory_cannot_be_absent():
    with pytest.raises(InterferencePolicyViewError, match="historical-memory"):
        _policy_candidates(SimpleNamespace(decision="INAPPLICABLE"), None, None)


def test_self_digest_checks_actual_content_not_claimed_green_flag():
    payload = {"preflight_passed": True}
    payload["report_digest"] = _digest(payload)
    assert _self_digest(payload, "report_digest") == payload["report_digest"]
    payload["preflight_passed"] = False
    with pytest.raises(InterferencePolicyViewError, match="mismatch"):
        _self_digest(payload, "report_digest")


def test_existing_policy_output_never_overwritten(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(InterferencePolicyViewError, match="must be new"):
        build_policy_views("missing-plan", "missing-preflight", "missing-manifest", output_dir=output)
    assert list(output.iterdir()) == []


def test_evaluation_child_helper_rejects_file_backed_database_before_any_mutation(tmp_path):
    with sqlite3.connect(tmp_path / "canonical.sqlite") as conn:
        with pytest.raises(InterferenceShadowViewError, match="RAM database"):
            _activate_evaluation_child(conn, None, None, None, None)
        assert list(conn.execute("SELECT name FROM sqlite_master")) == []


def test_runtime_generation_binding_pins_router_lifecycle_and_compiler_not_docs():
    binding = _runtime_code_binding()
    assert _self_digest(binding, "binding_digest") == binding["binding_digest"]
    files = {row["path"] for row in binding["files"]}
    for suffix in ("retrieval/memory_router.py", "knowledge/lifecycle.py",
                   "scripts/build_p13_interference_policy_views.py"):
        assert any(path.endswith(suffix) for path in files)
    assert not any("/docs/" in path for path in files)
