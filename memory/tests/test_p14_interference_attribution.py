"""P14 consumption guards; these fixtures are not empirical attribution."""
import json
import sqlite3
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import scripts.run_p14_interference_attribution as module
from scripts.run_p14_interference_attribution import (
    InterferenceAttributionError, _activate_existing_child, _candidate_matches,
    _changed_candidates, _state_load, run_interference_attribution,
)


def test_existing_output_is_preserved(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(InterferenceAttributionError, match="new and separate"):
        run_interference_attribution("missing", output_dir=output)
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("changed", [
    {"eligible_for_p14_attribution": False},
    {"evaluation_view_activation_performed_by_this_update": True},
    {"canonical_memory_mutation": "write"},
    {"production_runtime_imported": True},
    {"promotion_attempted": True},
])
def test_authority_boundary_rejected_before_opening_source(tmp_path, changed):
    raw = {"version": "p13-interference-source-bound-shadow-update-report-v1",
        "eligible_for_p14_attribution": True, "evaluation_view_activation_performed_by_this_update": False,
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
        "promotion_attempted": False, **changed}
    raw["report_digest"] = module._digest(raw)
    source = tmp_path / "p13.json"
    source.write_text(json.dumps(raw))
    with pytest.raises(InterferenceAttributionError, match="authority boundary"):
        run_interference_attribution(source, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_veto_cannot_alias_a_frozen_selected_candidate():
    with pytest.raises(InterferenceAttributionError, match="real veto"):
        _candidate_matches(None, {"candidate_digest": "selected"})
    assert _candidate_matches(None, None) is None


def test_selection_cannot_alias_frozen_candidate_absence():
    with pytest.raises(InterferenceAttributionError, match="real selection"):
        _candidate_matches(SimpleNamespace(), None)


def test_complete_candidate_content_must_match(monkeypatch):
    candidate = SimpleNamespace(candidate_digest="sha256:A", candidate_id="id", to_dict=lambda: {"id": "original"})
    monkeypatch.setattr(module, "_reference", lambda *args: (None, {"id": "changed"}))
    with pytest.raises(InterferenceAttributionError, match="exact executed"):
        _candidate_matches(candidate, {"candidate_digest": "sha256:A", "candidate_id": "id"})


def test_no_skill_uses_absence_witness_not_fabricated_candidate_lineage():
    before = {"case": {"candidate": {"id": "memory"}, "candidate_lineage": {"eligible": True}}}
    after = {"case": {"candidate": None, "candidate_absence_witness": {"execution": "actual"}}}
    assert _changed_candidates(before, after)
    after["case"]["candidate_absence_witness"] = None
    assert not _changed_candidates(before, after)


def test_file_backed_database_cannot_activate_evaluation_child(tmp_path):
    conn = sqlite3.connect(tmp_path / "db.sqlite")
    try:
        with pytest.raises(InterferenceAttributionError, match="requires RAM"):
            _activate_existing_child(conn, None, {}, {})
    finally:
        conn.close()


@pytest.mark.parametrize("matching", [True, False])
def test_state_load_replays_and_rolls_back_audit_rows(monkeypatch, matching):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE bookkeeping (value TEXT)")

    def resolve(connection, *args, **kwargs):
        assert kwargs["persist"] is True and kwargs["commit"] is False
        connection.execute("INSERT INTO bookkeeping VALUES ('temporary')")
        return SimpleNamespace(resolution_id="actual")

    monkeypatch.setattr(module, "resolve_current_state", resolve)
    monkeypatch.setattr(module, "verify_resolution_snapshot", lambda *args: SimpleNamespace(
        resolution_id="actual", to_dict=lambda: {"resolution_id": "actual"}))
    try:
        if matching:
            assert _state_load(conn, {}, "actual") == {"resolution_id": "actual"}
        else:
            with pytest.raises(InterferenceAttributionError, match="formal resolution"):
                _state_load(conn, {}, "other")
        assert conn.execute("SELECT COUNT(*) FROM bookkeeping").fetchone()[0] == 0
    finally:
        conn.close()


def test_unbound_no_memory_execution_cannot_witness_veto(monkeypatch):
    route = SimpleNamespace(decision="INAPPLICABLE", decision_digest="sha256:route", to_dict=lambda: {})
    monkeypatch.setattr(module, "_route_candidate", lambda *args: (route, None))
    query = {"query_plan": {}}
    audit = {"query": query, "route": {"decision_digest": "sha256:route"},
             "candidate_freeze": {"CAUSAL_NO_SKILL": None}}
    execution = SimpleNamespace(source="structured_memory", candidate_digest=module._digest({}),
                               action_digest=module._digest({}), outcome="PASS")
    row = {"authorities": {"Mt_plus_delta": {"cases": {"case": audit}}},
           "cohorts": {"Mt_plus_delta": SimpleNamespace(case_receipts={"case": SimpleNamespace(
               arm_receipts={"CAUSAL_NO_SKILL": execution})})}}
    with pytest.raises(InterferenceAttributionError, match="real successful veto fallback"):
        module._replay_view(None, {"heldout": row}, "Mt_plus_delta")


@pytest.mark.parametrize("overlap", ["lineage", "rtl", None])
def test_heldout_disjointness_checks_lineages_and_actual_rtl(overlap):
    training = {"train": {"lineage_id": "T", "rtl_sha256": ["sha256:T"]}}
    heldout = {"h1": {"lineage_id": "H1", "rtl_sha256": ["sha256:H1"]},
               "h2": {"lineage_id": "H2", "rtl_sha256": ["sha256:H2"]}}
    if overlap == "lineage":
        heldout["h1"]["lineage_id"] = "T"
    if overlap == "rtl":
        heldout["h1"]["rtl_sha256"] = ["sha256:T"]
    if overlap is None:
        assert module._heldout_disjoint_witness(training, heldout)["disjoint"] is True
    else:
        with pytest.raises(InterferenceAttributionError, match="overlaps training"):
            module._heldout_disjoint_witness(training, heldout)


@pytest.mark.parametrize("drift", ["digest", "resolution", "raw", "campaign", None])
def test_formal_rematerialization_requires_whole_staging_binding(monkeypatch, drift):
    from test_p13_interference_source_bound_shadow_update import _plan

    plan, _, _ = _plan()
    report = {"execution_bound_plan": {**plan.to_dict(), "plan_digest": plan.plan_digest},
              "execution_evidence": {"created_at": "2000-01-01T00:00:00+00:00"}}
    receipt = SimpleNamespace(plan_digest=plan.plan_digest, staging_digest_before="before",
        staging_digest_after="after", before_resolution_id="old", after_resolution_id="new",
        raw_evidence_after_digest="raw", metadata={"scope": {}, "training_evidence_campaigns": {}})
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE materialized (value TEXT)")

    def materialized(connection):
        return connection.execute("SELECT COUNT(*) FROM materialized").fetchone()[0] != 0

    def apply(connection, *args):
        connection.execute("INSERT INTO materialized VALUES ('child')")
        return {"wrong": "campaign"} if drift == "campaign" else {}

    monkeypatch.setattr(module, "_connection_digest", lambda c: (
        "wrong" if drift == "digest" else "after") if materialized(c) else "before")
    monkeypatch.setattr(module, "resolve_current_state", lambda c, *a, **kw: SimpleNamespace(
        resolution_id=("wrong" if drift == "resolution" else "new") if materialized(c) else "old"))
    monkeypatch.setattr(module, "raw_evidence_digest", lambda c: "wrong" if drift == "raw" else "raw")
    monkeypatch.setattr(module, "_scoped_replay", lambda *args: nullcontext())
    monkeypatch.setattr(module, "_apply_plan", apply)
    monkeypatch.setattr(module, "_state_load", lambda *args: {"load": "replayed"})
    try:
        if drift is None:
            before, after, load = module._materialize(conn, report, receipt)
            assert (before.resolution_id, after.resolution_id, load) == ("old", "new", {"load": "replayed"})
        else:
            with pytest.raises(InterferenceAttributionError, match="exactly rematerialize"):
                module._materialize(conn, report, receipt)
    finally:
        conn.close()
