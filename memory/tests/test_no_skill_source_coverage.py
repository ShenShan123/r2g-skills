"""Independent source-coverage engineering tests, not calibration samples."""
from __future__ import annotations

import copy
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from contracts import MemoryQuery
from tehm import db
from tehm.evaluation import no_skill_source_coverage as coverage
from tehm.evaluation.candidate_executor import execute_paired_candidates
from tehm.evaluation.no_skill_calibration import derive_no_skill_oracle_label, NoSkillCalibrationError
from tehm.knowledge import register_knowledge
from tehm.state import record_relation
from tehm.state.schema import ensure_state_schema
from test_asset_selector import _asset, _claim
from test_no_skill_calibration import _oracle_candidate


def _query(**extra):
    return MemoryQuery(query_plan={"mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1", "target_scope": "global", **extra})


def _derive(conn, query=None):
    return coverage.derive_no_skill_source_coverage(conn, campaign_id="coverage-campaign",
        case_id="coverage-case", query=query or _query())


@pytest.fixture
def source(tmp_tehm):
    conn, _, _ = tmp_tehm
    ensure_state_schema(conn)
    return conn


def _register(conn, **changes):
    # Direct validated status below is deliberate corrupt/insufficient source
    # setup. It must never be accepted as independently replayed authority.
    claim = replace(_claim(), status="shadow", **changes)
    register_knowledge(conn, claim, evidence_refs=[])
    return claim


def _validate_for_test(conn, identity):
    conn.execute("UPDATE tehm_mechanism_knowledge_status SET status='validated' WHERE knowledge_id=?",
                 (identity,))
    conn.commit()


def _case(receipt, query=None):
    return coverage.bind_no_skill_source_coverage_case({
        "case_id": "coverage-case", "campaign_id": "coverage-campaign",
        "split": "calibration", "learner_eligible": False,
        "toolchain_digest": "sha256:coverage-unit-toolchain",
        "oracle_digest": "sha256:coverage-unit-oracle",
    }, query=query or _query(), receipt=receipt)


def _pair(case, baseline="FAIL", forced="FAIL"):
    def oracle(candidate, _case, _budget):
        outcome = forced if candidate is not None else baseline
        return {"compile_result": "PASS", "functional_result": outcome,
                "signoff_result": "PASS" if outcome == "PASS" else "FAIL",
                "outcome": outcome, "oracle_digest": case["oracle_digest"]}
    candidate = _oracle_candidate()
    return execute_paired_candidates(case, {"NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
        "APPLICABILITY_GATED": candidate, "CAUSAL_NO_SKILL": candidate}, oracle=oracle,
        routing_decision="CONSIDER", budget=1)


def _label(conn, receipt, case, pair):
    return derive_no_skill_oracle_label(pair, source_coverage_receipt=receipt,
        source_connection=conn, source_query=_query(), frozen_case=case)


def test_complete_source_absence_replays_without_writes_or_router(source, monkeypatch):
    import tehm.retrieval.memory_router as router
    import tehm.retrieval.asset_selector as selector
    def reject(*_args, **_kwargs):
        raise AssertionError("coverage must not consume router/selector decisions")
    monkeypatch.setattr(router, "route_memory", reject)
    monkeypatch.setattr(selector, "select_knowledge_grounded_assets", reject)
    before = "\n".join(source.iterdump())
    receipt = _derive(source)
    assert receipt.audit["absence_established"] is True
    assert receipt.audit["candidate_binding_coverage"] == "NOT_ESTABLISHED"
    replay = coverage.verify_no_skill_source_coverage(source, receipt, query=_query())
    assert replay["verified"] is replay["absence_established"] is True
    assert "\n".join(source.iterdump()) == before
    assert source.execute("PRAGMA query_only").fetchone()[0] == 0
    assert source.in_transaction is False


def test_readonly_file_bytes_and_query_only_preserved(source, tmp_path):
    path = tmp_path / "frozen-source.sqlite"
    saved = sqlite3.connect(path)
    source.backup(saved); saved.close()
    before = path.read_bytes()
    conn = db.connect_read_only(path)
    conn.execute("PRAGMA query_only=ON")
    receipt = _derive(conn)
    assert coverage.verify_no_skill_source_coverage(conn, receipt, query=_query())["verified"]
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    conn.close()
    assert path.read_bytes() == before
    assert not Path(str(path) + "-wal").exists()


@pytest.mark.parametrize("kind", ["knowledge_table", "knowledge_status", "asset_table",
    "state_relations", "state_snapshot", "index", "altered", "wrong_version"])
def test_incomplete_or_altered_inventory_is_not_empty_coverage(source, kind):
    targets = {"knowledge_table": "tehm_mechanism_knowledge",
        "knowledge_status": "tehm_mechanism_knowledge_status", "asset_table": "tehm_assets",
        "state_relations": "tehm_memory_relations", "state_snapshot": "tehm_state_resolution_snapshots"}
    if kind in targets:
        source.execute(f"DROP TABLE {targets[kind]}")
    elif kind == "index":
        source.execute("DROP INDEX idx_memory_relations_source")
    elif kind == "altered":
        source.execute("ALTER TABLE tehm_mechanism_knowledge ADD COLUMN unowned TEXT")
    else:
        source.execute("UPDATE tehm_meta SET value='tehm-v0' WHERE key='schema_version'")
    source.commit()
    before = "\n".join(source.iterdump())
    receipt = _derive(source)
    assert receipt.audit["absence_established"] is False
    assert receipt.audit["inventory_complete"] is False
    assert receipt.audit["errors"]
    assert "\n".join(source.iterdump()) == before  # no lazy schema repair


@pytest.mark.parametrize("kind", ["content", "status", "missing_status", "blank_scope", "orphan_status"])
def test_corrupt_knowledge_in_any_family_blocks_absence(source, kind):
    claim = _register(source, mechanism_family="UNRELATED_FAMILY")
    if kind == "content":
        source.execute("UPDATE tehm_mechanism_knowledge SET antecedent_json='{}'")
    elif kind == "status":
        source.execute("UPDATE tehm_mechanism_knowledge_status SET status='unrecognised'")
    elif kind == "missing_status":
        source.execute("DELETE FROM tehm_mechanism_knowledge_status")
    elif kind == "blank_scope":
        source.execute("UPDATE tehm_mechanism_knowledge_status SET target_scope=' '")
    else:
        source.execute("UPDATE tehm_mechanism_knowledge_status SET knowledge_id='orphan'")
    source.commit()
    receipt = _derive(source)
    assert receipt.audit["absence_established"] is False
    assert receipt.audit["errors"]


@pytest.mark.parametrize("kind", ["content", "status", "missing_status", "orphan_status", "metadata"])
def test_corrupt_assets_cannot_disappear_behind_no_knowledge(source, kind):
    _asset(source)
    if kind == "content":
        source.execute("UPDATE tehm_assets SET definition_json='{}'")
    elif kind == "status":
        source.execute("PRAGMA ignore_check_constraints=ON")
        source.execute("UPDATE tehm_asset_status SET status='unknown'")
        source.execute("PRAGMA ignore_check_constraints=OFF")
    elif kind == "missing_status":
        source.execute("DELETE FROM tehm_asset_status")
    elif kind == "metadata":
        source.execute("UPDATE tehm_asset_status SET status_version=1.5")
    else:
        source.execute("UPDATE tehm_asset_status SET asset_id='orphan'")
    source.commit()
    receipt = _derive(source)
    assert receipt.audit["absence_established"] is False
    assert receipt.audit["errors"]


@pytest.mark.parametrize("kind", ["family", "profile", "not_validated", "foreign_scope"])
def test_complete_registry_exclusions_are_independently_observable(source, kind):
    changes = {"mechanism_family": "OTHER"} if kind == "family" else (
        {"compatibility_profile": "other-profile"} if kind == "profile" else {})
    claim = _register(source, **changes)
    if kind != "not_validated":
        _validate_for_test(source, claim.knowledge_id)
    if kind == "foreign_scope":
        source.execute("UPDATE tehm_mechanism_knowledge_status SET target_scope='other-scope'")
        source.commit()
    receipt = _derive(source)
    assert receipt.audit["absence_established"] is True
    assert receipt.inventory["knowledge_count"] == 1
    assert receipt.inventory["knowledge"][claim.object_id]["disposition"] in {
        "not_validated", "not_active_in_scope", "mechanism_or_profile_mismatch"}


@pytest.mark.parametrize("kind", ["authority", "positive_applicability", "negative_applicability", "measurement"])
def test_matching_uncertain_transfer_support_is_not_absence(source, kind):
    changes = {}
    if kind == "positive_applicability":
        changes["positive_applicability"] = ({"missing_context": True},)
    if kind == "negative_applicability":
        changes["negative_applicability"] = ({"mechanism_family": "HANDSHAKE_COMPLETION"},)
    if kind == "measurement":
        changes["intervention"] = {"measurement_contract": {"scope": "other", "contract_digest": "sha256:other"}}
    claim = _register(source, **changes)
    _validate_for_test(source, claim.knowledge_id)
    receipt = _derive(source)
    assert receipt.audit["absence_established"] is False
    assert receipt.audit["uncertainties"]


def test_scoped_lifecycle_overrides_global_validated_status(source):
    claim = _register(source)
    _validate_for_test(source, claim.knowledge_id)
    assert _derive(source).audit["absence_established"] is False
    source.execute("INSERT INTO tehm_mechanism_knowledge_status SELECT knowledge_id,version,"
        "'target-check','retired',2,provenance_json,updated_at FROM tehm_mechanism_knowledge_status")
    source.commit()
    receipt = _derive(source, _query(target_scope="target-check"))
    assert receipt.audit["absence_established"] is True
    assert receipt.inventory["knowledge"][claim.object_id]["disposition"] == "not_validated"


def test_unresolved_relations_do_not_prove_absence(source):
    record_relation(source, source_type="rule", source_id="missing-source",
        relation_type="SUPERSEDES", target_type="rule", target_id="missing-target",
        evidence_refs=("unknown-witness",))
    assert _derive(source).audit["absence_established"] is False


@pytest.mark.parametrize("key", ["routing_decision", "predicted_reason", "expected_reason",
    "confidence", "outcome", "gold_patch", "no_skill_reason", "state_shift_receipt", "out_of_distribution"])
def test_prediction_outcome_and_control_inputs_rejected_recursively(source, key):
    query = _query(ex_ante={"nested": {key.upper(): "not-source"}})
    with pytest.raises(coverage.SourceCoverageError, match="source-only"):
        _derive(source, query)


@pytest.mark.parametrize("key", ["mechanism_family", "compatibility_profile", "target_scope"])
def test_incomplete_query_cannot_be_inferred_from_router(source, key):
    query = _query(); del query.query_plan[key]
    with pytest.raises(coverage.SourceCoverageError, match="explicit"):
        _derive(source, query)


@pytest.mark.parametrize("field", ["source_memory_digest", "source_schema_digest", "absence", "query", "inventory"])
def test_rehashed_claim_is_not_source_replay(source, field):
    receipt = _derive(source); payload = receipt.to_dict()
    if field in {"source_memory_digest", "source_schema_digest"}:
        payload[field] = "sha256:" + "0" * 64
    elif field == "absence":
        payload["audit"]["absence_established"] = False
    elif field == "query":
        payload["source_query"]["query_plan"]["mechanism_family"] = "OTHER"
    else:
        payload["inventory"]["knowledge_count"] = 999
    assert coverage.verify_no_skill_source_coverage(source, payload, query=_query())["verified"] is False


def test_unrelated_sql_change_and_query_change_invalidate_frozen_source(source):
    receipt = _derive(source)
    source.execute("CREATE TABLE unrelated_evidence (payload TEXT)"); source.commit()
    assert not coverage.verify_no_skill_source_coverage(source, receipt, query=_query())["verified"]
    source.execute("DROP TABLE unrelated_evidence"); source.commit()
    assert coverage.verify_no_skill_source_coverage(source, receipt, query=_query())["verified"]
    assert not coverage.verify_no_skill_source_coverage(source, receipt, query=_query(target_scope="other"))["verified"]


def test_receipt_roundtrip_no_mutable_alias_and_rejects_extra_authority(source):
    receipt = _derive(source)
    payload = {**receipt.to_dict(), "receipt_id": receipt.receipt_id, "receipt_digest": receipt.receipt_digest}
    assert coverage.NoSkillSourceCoverageReceipt.from_dict(payload) == receipt
    payload["audit"]["runtime_profile"]["compatibility_mode"] = True
    assert receipt.audit["runtime_profile"]["compatibility_mode"] is False
    for changed in ({}, {**receipt.to_dict(), "admitted": True},
                    {**receipt.to_dict(), "receipt_digest": "sha256:" + "0" * 64}):
        assert coverage.verify_no_skill_source_coverage(source, changed, query=_query())["verified"] is False


@pytest.mark.parametrize("split", ["training", "heldout", "ab"])
def test_coverage_cannot_become_training_support(source, split):
    with pytest.raises(coverage.SourceCoverageError, match="calibration"):
        coverage.derive_no_skill_source_coverage(source, campaign_id="campaign", case_id="case", query=_query(), split=split)


@pytest.mark.parametrize("baseline,forced,expected", [("FAIL", "FAIL", "NO_MATCH"),
    ("PASS", "PASS", "NO_MATCH"), ("FAIL", "PASS", None), ("PASS", "FAIL", "RISK")])
def test_paired_labels_use_verified_coverage_without_overriding_benefit_or_harm(source, baseline, forced, expected):
    receipt = _derive(source); case = _case(receipt); pair = _pair(case, baseline, forced)
    label = _label(source, receipt, case, pair)
    assert label["expected_reason"] == expected
    assert label["expected_decision"] == ("USE_MEMORY" if expected is None else "NO_SKILL")
    assert label["confidence"] is None
    assert label["derivation"]["source_coverage_replay"]["verified"] is True
    assert label["derivation"]["router_prediction_used"] is False


def test_both_failures_with_uncertain_coverage_remain_unclassifiable(source):
    claim = _register(source); _validate_for_test(source, claim.knowledge_id)
    receipt = _derive(source); case = _case(receipt)
    label = _label(source, receipt, case, _pair(case))
    assert label["classifiable"] is False


@pytest.mark.parametrize("kind", ["missing", "post_execution", "case", "query", "source", "rehash"])
def test_paired_coverage_requires_actual_ex_ante_case_and_source_binding(source, kind):
    receipt = _derive(source); case = _case(receipt); pair = _pair(case)
    supplied = receipt
    if kind == "missing":
        del case["p15_source_coverage"]
    elif kind == "post_execution":
        original = copy.deepcopy(case); del original["p15_source_coverage"]; pair = _pair(original)
    elif kind == "case":
        case["case_id"] = "other"
    elif kind == "query":
        case["memory_query"]["query_plan"]["mechanism_family"] = "OTHER"
    elif kind == "source":
        source.execute("CREATE TABLE changed_source(payload TEXT)"); source.commit()
    else:
        supplied = receipt.to_dict(); supplied["audit"]["absence_established"] = False
    with pytest.raises(NoSkillCalibrationError, match="source coverage"):
        _label(source, supplied, case, pair)


@pytest.mark.parametrize("kind", ["learner", "partition", "campaign", "compatibility"])
def test_case_binding_cannot_claim_another_profile_or_membership(source, kind):
    receipt = _derive(source); case = _case(receipt)
    if kind == "learner": case["learner_eligible"] = True
    elif kind == "partition": case["split"] = "training"
    elif kind == "campaign": case["campaign_id"] = "other"
    else: case["p15_source_coverage"]["runtime_profile"]["compatibility_mode"] = True
    with pytest.raises(coverage.SourceCoverageError):
        coverage.bind_no_skill_source_coverage_case(case, query=_query(), receipt=receipt)


def test_source_coverage_does_not_waive_unknown_signoff(source):
    receipt = _derive(source); case = _case(receipt); pair = _pair(case)
    pair = replace(pair, arm_receipts={**pair.arm_receipts,
        "ALWAYS_MEMORY": replace(pair.arm_receipts["ALWAYS_MEMORY"], signoff_result="UNKNOWN")})
    with pytest.raises(NoSkillCalibrationError, match="incomplete"):
        _label(source, receipt, case, pair)


def test_case_and_receipt_do_not_expose_shared_runtime_profile(source):
    receipt = _derive(source)
    case = _case(receipt)
    case["p15_source_coverage"]["runtime_profile"]["compatibility_mode"] = True
    assert receipt.audit["runtime_profile"]["compatibility_mode"] is False
    assert _derive(source).audit["runtime_profile"]["compatibility_mode"] is False


def test_numeric_boolean_profile_substitution_does_not_replay(source):
    receipt = _derive(source)
    payload = receipt.to_dict(); payload["audit"]["runtime_profile"]["compatibility_mode"] = 0
    assert not coverage.verify_no_skill_source_coverage(source, payload, query=_query())["verified"]


def test_inventory_audit_preserves_outer_transaction(source):
    source.execute("BEGIN")
    source.execute("INSERT INTO tehm_meta VALUES ('pending-unit', 'must-not-be-committed')")
    before = "\n".join(source.iterdump())
    assert _derive(source).audit["absence_established"]
    assert source.in_transaction and "\n".join(source.iterdump()) == before
    source.rollback()


def test_explicit_unbounded_profile_does_not_exclude_matching_knowledge(source):
    query = _query(compatibility_profile=None)
    assert _derive(source, query).audit["absence_established"] is True
    claim = _register(source); _validate_for_test(source, claim.knowledge_id)
    receipt = _derive(source, query)
    assert receipt.audit["absence_established"] is False
    assert receipt.audit["uncertainties"]
    assert receipt.inventory["knowledge"][claim.object_id]["disposition"] != "mechanism_or_profile_mismatch"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_source_query_is_not_typed_json(source, value):
    with pytest.raises(coverage.SourceCoverageError, match="finite JSON"):
        _derive(source, _query(ex_ante={"not_finite": value}))


@pytest.mark.parametrize("key", ["ShouldUseMemory", "oracleLabel", "no-skill-reason", "GoldPatch", " confidence "])
def test_prediction_key_aliases_are_not_source_context(source, key):
    with pytest.raises(coverage.SourceCoverageError, match="source-only"):
        _derive(source, _query(ex_ante={key: "not-source"}))


def test_case_binding_requires_byte_exact_source_query(source):
    query = _query(ex_ante={"observed_flag": False})
    receipt = _derive(source, query)
    case = _case(receipt, query)
    changed = _query(ex_ante={"observed_flag": 0})
    with pytest.raises(coverage.SourceCoverageError):
        coverage.bind_no_skill_source_coverage_case(case, query=changed, receipt=receipt)
