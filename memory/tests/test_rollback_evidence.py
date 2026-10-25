"""P1b / D2: rollback traceability in canonical transitions (protocol v1 section 8.2).

A rollback is bound to the action instance; it never makes a result positive, and its evidence
class follows the verified result (budget/accounting/timeouts -> UNKNOWN). Legacy records without
the marker read UNKNOWN and keep their content-hash identity.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tehm.canonical.transition import ObservationDelta  # noqa: E402
from tehm.ids import transition_id  # noqa: E402

_spec = importlib.util.spec_from_file_location("prm", Path(__file__).with_name("test_r2g_parametric_rules.py"))
prm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prm)


def test_legacy_delta_without_marker_keeps_its_identity_and_reads_unknown():
    legacy = {"original_failure": "PRESENT", "first_divergence": {"before": 4, "after": 2},
              "failing_tests": {"before": 1, "after": 1}, "created_regressions": [],
              "newly_observed_failures": [], "experiment_kind": "REPAIR", "utility_verdict": "UNKNOWN"}
    d = ObservationDelta.from_dict(legacy)
    assert d.rolled_back == "UNKNOWN" and d.to_dict() == legacy        # serialised exactly as before
    ids = dict(source_state_id="s", target_state_id="t", action={"a": 1}, verifier={"v": 1})
    assert transition_id(observation_delta=d.to_dict(), **ids) == transition_id(observation_delta=legacy, **ids)


def test_known_marker_round_trips():
    d = ObservationDelta.from_dict({"original_failure": "PRESENT", "rolled_back": "true",
                                    "rollback_reason": "regression"})
    assert d.to_dict()["rolled_back"] == "true" and d.to_dict()["rollback_reason"] == "regression"


def _delta(**row):
    return prm._transition(prm._row(**row), "a", "t")["observation_delta"]


def test_rows_carry_their_rollback_marker():
    reg = _delta(after=6, verdict="regression", rolled_back=True, rollback_reason="regression")
    assert reg["rolled_back"] == "true" and reg["rollback_reason"] == "regression"
    keep = _delta(after=4, verdict="no_improvement", rolled_back=False)       # catalogue: edit persists
    assert keep["rolled_back"] == "false"
    legacy = _delta(after=2, verdict="applied", rolled_back=None)             # legacy row: no marker
    assert legacy["rolled_back"] == "UNKNOWN"
    assert "rolled_back" not in ObservationDelta.from_dict(legacy).to_dict()  # stored without it


def test_budget_refusal_is_unknown_evidence_even_when_rolled_back():
    t = prm._transition(prm._row(after=4, verdict="budget_exhausted", rolled_back=True,
                                 rollback_reason="budget_exhausted"), "a", "t")
    assert t["verifier"]["verdict"] == "UNKNOWN" and t["verifier"]["oracle_complete"] is False
    assert t["observation_delta"]["rolled_back"] == "true"


def test_a_marked_transition_is_a_valid_witness_and_crystallises(tmp_path):
    # P1b review 2026-10-09: the load-time validator dropped rolled_back / rollback_reason, so every
    # marked transition failed its content-address check and could never become rule evidence
    import test_r2g_store_vetoes as V
    from tehm.crystallization.build_rules import require_verified_transition
    from tehm_backend import TehmMemoryBackend
    b = TehmMemoryBackend(db_path=tmp_path / "t.sqlite", artifact_root=tmp_path / "a")
    try:
        for d, (ub, ua) in {"d1": ("12", "8"), "d2": ("11", "7"), "d3": ("10", "6")}.items():
            b.ingest_project(V._project(tmp_path, d, [{**V._fix("density_relief", 4, 0, "cleared", V.SIT, ub, ua),
                                                       "rolled_back": False}]))
        conn, _ = b._open()
        for (tid, delta) in conn.execute("SELECT transition_id, observation_delta_json FROM tehm_transitions"):
            assert json.loads(delta)["rolled_back"] == "false"
            require_verified_transition(conn, tid)                       # no content-address mismatch
        b.rebuild()
        assert conn.execute("SELECT count(*) FROM tehm_rules").fetchone()[0] >= 1
    finally:
        b.close()
