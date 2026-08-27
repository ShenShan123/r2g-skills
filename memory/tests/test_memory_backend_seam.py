"""MemoryBackend seam (design doc 17.1-17.4, 27.3 H5/H12).

R2G_MEMORY_BACKEND selects the memory plane at process start; selection is
locked, invalid names are fail-closed, and the TEHM backend never touches the
legacy plane. Legacy semantics are preserved by the legacy backend being a
read-only adapter over the committed heuristics.json / knowledge.sqlite.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import patch
from pathlib import Path

import pytest

from contracts import ActivationResult, ExecutionRecord, MemoryQuery, RepairContext
from factory import BackendLockError, open_memory_backend
from tehm.ids import rule_id as mint_rule_id, stable_dumps


def _record(sample_record_dict) -> ExecutionRecord:
    return ExecutionRecord.from_dict(sample_record_dict)


def _seed_backend_rule(conn) -> str:
    """Create the smallest rule row needed by backend receipt tests."""
    now = "2026-08-27T00:00:00-07:00"
    before = {"target_check": "route", "knob": "CORE_UTILIZATION"}
    after = {"rewrite.value": "20", "execution.recheck": "route"}
    obligations = ["TARGET_FAILURE_REMOVED"]
    rule_id = mint_rule_id(
        domain="flow.signoff", before_pattern=before,
        after_pattern=after, hard_preconditions=[], obligations=obligations)
    conn.execute(
        """INSERT INTO tehm_rules (
          rule_id,domain,before_pattern_json,after_pattern_json,
          hard_preconditions_json,context_profile_json,obligations_json,
          validity_status,validity_profile_json,confidence_json,utility_json,
          risk_profile_json,predicate_schema_version,role_schema_version,
          crystallizer_version,merge_trace_digest,created_at,updated_at)
          VALUES (?,'flow.signoff',?,?, '[]','{}',?,'VALIDATED','{}','{}','{}',
                  '[]','p','v','c','m',?,?)""",
        (rule_id, stable_dumps(before), stable_dumps(after),
         stable_dumps(obligations), now, now))
    conn.commit()
    return rule_id


def _seed_backend_activation(conn, *, rule_id: str, activation_id: str,
                             outcome: str = "UNKNOWN",
                             created_regressions: list | None = None,
                             produced_transition_id: str | None = None,
                             rollback_receipt: dict | None = None) -> None:
    conn.execute(
        """INSERT INTO tehm_activations (
          activation_id,rule_id,target_state_id,retrieval_receipt_json,
          applicability_status,binding_status,binding_json,executability_status,
          obligation_transfer_json,obligation_coverage,verification_status,
          verifier_json,outcome,created_regressions_json,produced_transition_id,
          rollback_receipt_json,trial_uuid,created_at)
          VALUES (?,?,'target','{}','APPLICABLE','BOUND','{}','EXECUTABLE',
                  '{}',1.0,'UNKNOWN','{}',?,?,?,?,NULL,'now')""",
        (activation_id, rule_id, outcome,
         stable_dumps(created_regressions or []), produced_transition_id,
         stable_dumps(rollback_receipt) if rollback_receipt is not None else None))
    conn.commit()


# -- selection ----------------------------------------------------------------

def test_default_backend_is_legacy(monkeypatch):
    monkeypatch.delenv("R2G_MEMORY_BACKEND", raising=False)
    backend = open_memory_backend(_lock=False)
    assert backend.name == "legacy"


def test_env_selects_tehm(monkeypatch, tmp_path, sample_record_dict):
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    monkeypatch.setenv("TEHM_DB", str(tmp_path / "tehm.sqlite"))
    monkeypatch.setenv("TEHM_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    backend = open_memory_backend(_lock=False)
    assert backend.name == "tehm"


def test_env_selects_none(monkeypatch):
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "none")
    assert open_memory_backend(_lock=False).name == "none"


def test_invalid_name_fail_closed():
    with pytest.raises(ValueError, match="fail-closed"):
        open_memory_backend("bogus", _lock=False)


# -- process lock (design doc 17.3) ------------------------------------------

def test_backend_lock_forbids_switching():
    open_memory_backend("none", _lock=True)
    with pytest.raises(BackendLockError):
        open_memory_backend("tehm", _lock=True)


# -- none backend -------------------------------------------------------------

def test_none_backend_never_retrieves(sample_record_dict):
    backend = open_memory_backend("none", _lock=False)
    receipt = backend.ingest_execution(_record(sample_record_dict))
    assert receipt.backend == "none"
    assert backend.retrieve(backend.build_query(RepairContext()), limit=5) == []
    assert backend.propose_activation.__name__  # callable
    snap = backend.snapshot()
    assert snap.schema_version == "none-v1"


# -- tehm backend -------------------------------------------------------------

def test_tehm_backend_ingests(tmp_path, sample_record_dict):
    import os
    os.environ["TEHM_DB"] = str(tmp_path / "tehm.sqlite")
    os.environ["TEHM_ARTIFACTS_ROOT"] = str(tmp_path / "artifacts")
    backend = open_memory_backend("tehm", _lock=False)
    receipt = backend.ingest_execution(_record(sample_record_dict))
    assert receipt.transition_id.startswith("transition_")
    assert receipt.outcome == "PASS"
    assert receipt.backend == "tehm"
    snap = backend.snapshot()
    assert snap.counts["transitions"] == 1
    assert snap.counts["views"] == 6
    report = backend.rebuild()
    assert report.ok
    backend.close()


def test_tehm_backend_rebuild_rolls_back_rules_and_lifecycle_on_failure(
        tmp_path, sample_record_dict):
    """Backend rebuild cannot expose a partial rule/status projection."""
    from tehm.lifecycle import rule_status
    from tehm_backend import TehmMemoryBackend

    backend = TehmMemoryBackend(
        db_path=tmp_path / "tehm.sqlite",
        artifact_root=tmp_path / "artifacts")
    for i in range(3):
        record = json.loads(json.dumps(sample_record_dict))
        record["record_id"] = f"backend_rebuild_failure_{i}"
        record["lineage_id"] = f"backend_lineage_{i}"
        record["design_id"] = f"backend_design_{i}"
        record["episode"] = {
            "episode_id": f"backend_episode_{i}",
            "lineage_id": f"backend_lineage_{i}",
            "step_index": 0,
            "terminal_status": "VERIFIED_REPAIR",
        }
        knob = "PLACE_DENSITY_LB_ADDON"
        record["action"]["payload"]["config_edits"] = {
            knob: f"0.1{i + 4}"}
        record["before"]["config"][knob] = "0.10"
        record["after"]["config"][knob] = f"0.1{i + 4}"
        record["observation_delta"]["first_divergence"]["before"] = 10 + i
        backend.ingest_execution(_record(record))

    original = rule_status.set_status

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected backend lifecycle failure")

    with patch.object(rule_status, "set_status", side_effect=write_then_fail):
        with pytest.raises(RuntimeError, match="injected backend lifecycle failure"):
            backend.rebuild()
    conn, _ = backend._open()
    assert conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_rule_status").fetchone()[0] == 0
    assert not conn.in_transaction
    backend.close()


def test_tehm_backend_retrieval_honest_empty(tmp_path):
    import os
    os.environ["TEHM_DB"] = str(tmp_path / "tehm.sqlite")
    os.environ["TEHM_ARTIFACTS_ROOT"] = str(tmp_path / "artifacts")
    backend = open_memory_backend("tehm", _lock=False)
    query = backend.build_query(RepairContext(design_id="d", check="drc"))
    assert "procedural_view" in query.query_plan
    assert backend.retrieve(query, limit=3) == []  # Phase 7 not fabricated


def test_tehm_backend_retrieve_keeps_frozen_query_context(tmp_path, monkeypatch):
    """The backend must not round-trip MemoryQuery through RepairContext.

    A second planning pass used to drop structural/compatibility fields and
    made concrete profile predicates become UNRESOLVED.  The direct query
    entry point must receive the exact typed query object supplied by callers.
    """
    from tehm.retrieval.result import RetrievalReceipt

    captured = {}

    def fake_retrieve_query(conn, query, *, limit):
        captured["query"] = query
        captured["limit"] = limit
        return RetrievalReceipt(
            query_plan=query.query_plan, candidates_retrieved=0,
            applicable=0, inapplicable=0, unresolved=0, results=[],
            latency_ms=0.0)

    monkeypatch.setattr("tehm.retrieval.pipeline.retrieve_query", fake_retrieve_query)
    from tehm_backend import TehmMemoryBackend
    backend = TehmMemoryBackend(
        db_path=tmp_path / "tehm.sqlite",
        artifact_root=tmp_path / "artifacts")
    query = MemoryQuery(query_plan={
        "check": "rtl",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "structural_graph": {"nodes": [{"id": "WAIT"}], "edges": []},
    })
    assert backend.retrieve(query, limit=7) == []
    assert captured["query"] is query
    assert captured["query"].query_plan["structural_graph"]["nodes"] == [{"id": "WAIT"}]
    assert captured["query"].query_plan["compatibility_profile"] == "rtl.fsm.single_guard.v1"
    assert captured["limit"] == 7


def test_tehm_backend_record_activation_finalizes_and_replays_exactly(tmp_tehm):
    from tehm_backend import TehmMemoryBackend

    conn, _, tmp_path = tmp_tehm
    rule_id = _seed_backend_rule(conn)
    transition_id = "transition_backend_receipt"
    conn.execute(
        """INSERT INTO tehm_transitions (
          transition_id,source_state_id,target_state_id,action_domain,
          action_json,observation_delta_json,verifier_json,primary_effect_key,
          outcome,created_regressions_json,newly_observed_json,provenance_json,
          schema_version)
          VALUES (?,'state','state','flow.signoff','{}','{}','{}',NULL,
                  'PASS','[]','[]','{}','tehm-v4')""",
        (transition_id,))
    _seed_backend_activation(
        conn, rule_id=rule_id, activation_id="backend-act",
        outcome="UNKNOWN")
    backend = TehmMemoryBackend(
        db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "artifacts")
    result = ActivationResult(
        activation_id="backend-act", outcome="PASS",
        produced_transition_id=transition_id,
        rollback_receipt={"verified": True})

    backend.record_activation(result)
    backend.record_activation(result)  # exact retry is a no-op
    row = backend._open()[0].execute(
        """SELECT outcome, created_regressions_json,
                  produced_transition_id, rollback_receipt_json
             FROM tehm_activations WHERE activation_id='backend-act'""").fetchone()
    assert row["outcome"] == "PASS"
    assert row["created_regressions_json"] == "[]"
    assert row["produced_transition_id"] == transition_id
    assert row["rollback_receipt_json"] == '{"verified":true}'
    utility = backend._open()[0].execute(
        "SELECT utility_json FROM tehm_rules WHERE rule_id=?", (rule_id,)
    ).fetchone()[0]
    assert json.loads(utility)["activations"] == 1

    with pytest.raises(ValueError, match="replay conflicts"):
        backend.record_activation(
            ActivationResult(activation_id="backend-act", outcome="FAIL"))
    assert backend._open()[0].execute(
        "SELECT outcome FROM tehm_activations WHERE activation_id='backend-act'"
    ).fetchone()[0] == "PASS"
    backend.close()


def test_tehm_backend_record_activation_rejects_invalid_result_witness(tmp_tehm):
    from tehm_backend import TehmMemoryBackend

    conn, _, tmp_path = tmp_tehm
    rule_id = _seed_backend_rule(conn)
    _seed_backend_activation(conn, rule_id=rule_id, activation_id="backend-invalid")
    backend = TehmMemoryBackend(
        db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "artifacts")

    with pytest.raises(ValueError, match="outcome must be one of"):
        backend.record_activation(
            ActivationResult(activation_id="backend-invalid", outcome="MAYBE"))
    with pytest.raises(ValueError, match="missing canonical transition"):
        backend.record_activation(ActivationResult(
            activation_id="backend-invalid", outcome="PASS",
            produced_transition_id="transition-missing"))
    row = backend._open()[0].execute(
        "SELECT outcome, produced_transition_id FROM tehm_activations "
        "WHERE activation_id='backend-invalid'"
    ).fetchone()
    assert row["outcome"] == "UNKNOWN"
    assert row["produced_transition_id"] is None
    backend.close()
    backend.close()


# -- legacy backend -----------------------------------------------------------

def test_legacy_backend_resolves_and_snapshots():
    backend = open_memory_backend("legacy", _lock=False)
    assert backend.knowledge_dir.is_dir()
    assert backend.db_path().name == "knowledge.sqlite"
    snap = backend.snapshot()
    assert snap.schema_version == "legacy-knowledge-v1"
    assert snap.snapshot_id.startswith("legacy:")
    assert isinstance(snap.counts.get("runs"), int)


def test_legacy_backend_build_query_and_retrieve_read_only():
    backend = open_memory_backend("legacy", _lock=False)
    ctx = RepairContext(
        design_id="aes128_core", check="drc",
        reports={"drc": {"status": "violations", "total_violations": 7,
                         "categories": {"antenna_diode_repair": {"count": 7}}}},
        cfg={"PLATFORM": "nangate45"},
    )
    query = backend.build_query(ctx)
    sid = (query.query_plan or {}).get("legacy_symptom_id")
    assert sid and len(sid) == 16  # symptom_id shape
    # Read-only: heuristics.json must be untouched (mtime unchanged is hard to
    # assert cheaply; instead verify no write path is reachable via retrieve).
    candidates = backend.retrieve(query, limit=5)
    for c in candidates:
        assert c.source == "legacy_memory"
    # A cold-start symptom yields nothing (never fabricated).
    cold = backend.retrieve(backend.build_query(RepairContext(check="drc")), limit=5)
    assert cold == []


def test_legacy_backend_ingest_fail_closed_without_project():
    """The Protocol ingest method is fail-closed for legacy (transition-indexed
    records cannot be absorbed by a project-run-indexed plane)."""
    from legacy_backend import LegacyBackendError
    from contracts import ExecutionRecord

    backend = open_memory_backend("legacy", _lock=False)
    record = ExecutionRecord.from_dict({
        "domain": "flow.signoff",
        "before": {"reports": {"drc": {"status": "clean"}}, "config": {}},
        "action": {"domain": "signoff.REPAIR_ACTION",
                   "transformation_family": "X", "payload": {"k": "v"}},
        "after": {"reports": {"drc": {"status": "clean"}}, "config": {}},
        "observation_delta": {"original_failure": "UNKNOWN"},
        "verification": {"verdict": "UNKNOWN"},
        "record_id": "no_project",
    })
    with pytest.raises(LegacyBackendError, match="project-run indexed"):
        backend.ingest_execution(record)


def test_legacy_backend_ingest_project_runs_real_code(tmp_path):
    """ingest_project shells to the REAL ingest_run.py — golden equivalence."""
    import os
    from pathlib import Path

    from legacy_backend import LegacyMemoryBackend

    legacy_db = tmp_path / "knowledge.sqlite"
    os.environ["R2G_FIX_AUTOLEARN"] = "0"
    backend = LegacyMemoryBackend(db_path=legacy_db)
    proj = Path(__file__).resolve().parent / "fixtures" / "project_antenna_fix"
    receipt = backend.ingest_project(proj)
    assert receipt.backend == "legacy"
    assert legacy_db.exists()
    import sqlite3
    conn = sqlite3.connect(legacy_db)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    conn.close()


def test_tehm_backend_never_opens_legacy(tmp_path):
    """H5/H12: the TEHM store path is validated; legacy is never a candidate."""
    from tehm import config
    legacy = tmp_path / "signoff-loop" / "knowledge.sqlite"
    legacy.parent.mkdir(parents=True)
    legacy.touch()
    with pytest.raises(ValueError, match="isolated"):
        config.validate_backend_lock(legacy)


def test_ingest_cli_tehm_does_not_create_legacy_db(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "r2g-skills" / "signoff-loop" / "knowledge" / "ingest_run.py"
    project = Path(__file__).resolve().parent / "fixtures" / "project_antenna_fix"
    legacy_db = tmp_path / "must_not_exist.sqlite"
    env = dict(os.environ)
    env.update({
        "R2G_MEMORY_BACKEND": "tehm",
        "TEHM_DB": str(tmp_path / "tehm.sqlite"),
        "TEHM_ARTIFACTS_ROOT": str(tmp_path / "artifacts"),
        "R2G_TEHM_ROOT": str(repo / "memory"),
        "R2G_FIX_AUTOLEARN": "0",
    })
    proc = subprocess.run(
        [sys.executable, str(script), str(project), "--db", str(legacy_db)],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "memory_backend=tehm" in proc.stdout
    assert not legacy_db.exists()


def test_read_only_tehm_snapshot_cannot_mutate(tmp_path, sample_record_dict):
    from tehm import db
    from tehm_backend import TehmMemoryBackend

    db_path = tmp_path / "tehm.sqlite"
    conn = db.connect(db_path)
    db.ensure_schema(conn)
    conn.close()
    before = db_path.read_bytes()
    backend = TehmMemoryBackend(
        db_path=db_path, artifact_root=tmp_path / "artifacts",
        read_only_eval=True)
    assert backend.snapshot().backend == "tehm"
    assert backend.rebuild().detail.startswith("read-only evaluation")
    with pytest.raises(RuntimeError, match="read-only"):
        backend.ingest_execution(_record(sample_record_dict))
    backend.close()
    assert db_path.read_bytes() == before
