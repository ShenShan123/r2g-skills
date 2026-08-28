"""Rule lifecycle + A/B (design doc 20.10, 24.3, 26 Phase 9; test list 27.1).

Validity-gated entry into shadow (H6); monotonic status_version; variance-aware
A/B judging; promotion authority refuses stale / non-differing / regression /
low-coverage trials.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts import RepairContext
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.crystallization.build_rules import crystallize_all
from tehm.lifecycle.authority import apply_trial_verdict
from tehm.lifecycle.rule_status import (
    RuleLifecycleError,
    enter_shadow,
    get_status,
    set_status,
)
from tehm.lifecycle.trial_adapter import (
    TEHMRuleTrialSubject,
    judge_trial,
    lcb,
    record_external_trial,
    run_trial,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _crystallize_valid_rule(tmp_tehm, sample_record_dict) -> str:
    conn, store, _ = tmp_tehm
    base = json.loads(json.dumps(sample_record_dict))
    for i in range(3):
        rec = json.loads(json.dumps(base))
        rec["record_id"] = f"lf_{i}"
        rec["lineage_id"] = f"lineage_{i}"
        rec["episode"] = {"episode_id": f"ep_lf_{i}", "lineage_id": f"lineage_{i}",
                          "step_index": 0, "terminal_status": "VERIFIED_REPAIR"}
        rec["action"]["payload"]["config_edits"] = {"PLACE_DENSITY_LB_ADDON": f"0.1{i + 4}"}
        rec["before"]["config"]["PLACE_DENSITY_LB_ADDON"] = "0.10"
        rec["after"]["config"]["PLACE_DENSITY_LB_ADDON"] = f"0.1{i + 4}"
        rec["observation_delta"]["first_divergence"]["before"] = 10 + i
        capture(conn, store, ExecutionRecord.from_dict(rec))
    rules = crystallize_all(conn)
    return rules[0]["rule_id"]


# -- rule status ----------------------------------------------------------------

def test_enter_shadow_validity_gated(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    valid_rule = _crystallize_valid_rule(tmp_tehm, sample_record_dict)
    version = enter_shadow(conn, rule_id=valid_rule, target_scope="drc",
                           provenance={"source": "test"})
    assert version == 1
    status = get_status(conn, rule_id=valid_rule, target_scope="drc")
    assert status["status"] == "shadow"
    assert status["status_version"] == 1

    # a below-validity rule is refused (H6)
    conn.execute(
        """INSERT OR REPLACE INTO tehm_rules (
               rule_id, domain, before_pattern_json, after_pattern_json,
               hard_preconditions_json, context_profile_json, obligations_json,
               validity_status, validity_profile_json, confidence_json,
               utility_json, risk_profile_json, predicate_schema_version,
               role_schema_version, crystallizer_version, merge_trace_digest,
               created_at, updated_at)
           VALUES (?, 'flow.signoff', '{}', '{}', '[]', '{}', '[]',
               'REJECT_DEGENERATE', '{}', '{}', '{}', '[]',
               'predicate-v0.1', 'role-v0.1', 'x', 'x', '', '')""",
        ("rule_bad",))
    conn.commit()
    with pytest.raises(RuleLifecycleError, match="H6"):
        enter_shadow(conn, rule_id="rule_bad", target_scope="drc")


def test_status_version_bumps_on_every_transition(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_valid_rule(tmp_tehm, sample_record_dict)
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    set_status(conn, rule_id=rule_id, target_scope="drc", status="candidate")
    set_status(conn, rule_id=rule_id, target_scope="drc", status="promoted")
    status = get_status(conn, rule_id=rule_id, target_scope="drc")
    assert status["status_version"] == 3


def test_status_replay_is_idempotent_but_provenance_immutable(
        tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_valid_rule(tmp_tehm, sample_record_dict)
    first = enter_shadow(
        conn, rule_id=rule_id, target_scope="drc",
        provenance={"source": "lifecycle-test"})
    replay = set_status(
        conn, rule_id=rule_id, target_scope="drc", status="shadow",
        provenance={"source": "lifecycle-test"})
    assert replay == first
    with pytest.raises(RuleLifecycleError, match="immutable provenance"):
        set_status(
            conn, rule_id=rule_id, target_scope="drc", status="shadow",
            provenance={"source": "tampered"})
    row = conn.execute(
        "SELECT status, status_version, provenance_json "
        "FROM tehm_rule_status WHERE rule_id=? AND target_scope=?",
        (rule_id, "drc")).fetchone()
    assert row["status"] == "shadow"
    assert row["status_version"] == 1
    assert json.loads(row["provenance_json"]) == {
        "source": "lifecycle-test"}


def test_status_reader_fails_closed_on_malformed_provenance(
        tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_valid_rule(tmp_tehm, sample_record_dict)
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    conn.execute(
        "UPDATE tehm_rule_status SET provenance_json=? "
        "WHERE rule_id=? AND target_scope=?",
        ("[]", rule_id, "drc"))
    conn.commit()
    with pytest.raises(RuleLifecycleError, match="malformed provenance"):
        get_status(conn, rule_id=rule_id, target_scope="drc")


def test_status_reader_fails_closed_on_weakly_typed_version(
        tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_valid_rule(tmp_tehm, sample_record_dict)
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    # SQLite's dynamic typing permits a copied/altered status row to store a
    # string in the INTEGER version column when checks are bypassed.  A
    # lifecycle replay must not coerce that text into a valid version.
    conn.execute("PRAGMA ignore_check_constraints=ON")
    conn.execute(
        "UPDATE tehm_rule_status SET status_version='version-1' "
        "WHERE rule_id=? AND target_scope=?", (rule_id, "drc"))
    conn.commit()
    with pytest.raises(RuleLifecycleError, match="invalid status_version"):
        get_status(conn, rule_id=rule_id, target_scope="drc")


# -- A/B judging -----------------------------------------------------------------

def test_lcb_variance_aware():
    assert lcb([1.0, 1.0, 1.0], z=1.0) == 1.0
    assert lcb([0.0, 0.0, 0.0], z=1.0) == 0.0
    assert lcb([1.0]) < 1.0  # one sample -> maximal uncertainty (0.5 se)


def test_judge_trial_verdicts():
    assert judge_trial([0.0, 0.0], [1.0, 1.0])[0] == "win"
    assert judge_trial([1.0, 1.0], [0.0, 0.0])[0] == "loss"
    assert judge_trial([0.0, 1.0], [0.0, 1.0])[0] == "inconclusive"


def test_run_trial_records_tehm_trials(tmp_tehm):
    conn, _, _ = tmp_tehm
    subject = TEHMRuleTrialSubject(rule_id="rule_x", status_version=1)

    def arm_a(plan, ctx):   # control fails
        return {"success": False}

    def arm_b(plan, ctx):   # rule succeeds
        return {"success": True}

    trial = run_trial(conn, subject=subject, context=RepairContext(check="drc"),
                      arm_a_evaluator=arm_a, arm_b_evaluator=arm_b, repeats=2,
                      trial_uuid="u1")
    assert trial["verdict"] == "win"
    row = conn.execute(
        "SELECT verdict, rule_id, status_version FROM tehm_trials WHERE trial_uuid='u1'"
    ).fetchone()
    assert row["verdict"] == "win"
    assert row["rule_id"] == "rule_x"


# -- promotion authority ----------------------------------------------------------

def test_authority_promotes_on_win(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_valid_rule(tmp_tehm, sample_record_dict)
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    set_status(conn, rule_id=rule_id, target_scope="drc", status="candidate")
    version = get_status(conn, rule_id=rule_id, target_scope="drc")["status_version"]
    new_status = apply_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="win",
        obligation_coverage=1.0, created_regressions=[], arms_differ=True,
        expected_status_version=version)
    assert new_status == "promoted"
    assert get_status(conn, rule_id=rule_id, target_scope="drc")["status"] == "promoted"


def test_authority_demotes_on_loss(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_valid_rule(tmp_tehm, sample_record_dict)
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    version = get_status(conn, rule_id=rule_id, target_scope="drc")["status_version"]
    new_status = apply_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="loss",
        obligation_coverage=1.0, created_regressions=[], arms_differ=True,
        expected_status_version=version)
    assert new_status == "demoted"


def test_authority_refuses_stale_or_non_differing_or_regression(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_valid_rule(tmp_tehm, sample_record_dict)
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    version = get_status(conn, rule_id=rule_id, target_scope="drc")["status_version"]

    # stale version
    assert apply_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="win",
        obligation_coverage=1.0, created_regressions=[], arms_differ=True,
        expected_status_version=version + 99) is None
    # arms did identical work
    assert apply_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="win",
        obligation_coverage=1.0, created_regressions=[], arms_differ=False,
        expected_status_version=version) is None
    # hard regression
    assert apply_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="win",
        obligation_coverage=1.0, created_regressions=["lvs"],
        arms_differ=True, expected_status_version=version) is None
    # insufficient obligation coverage
    assert apply_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="win",
        obligation_coverage=0.2, created_regressions=[], arms_differ=True,
        expected_status_version=version) is None
    # inconclusive leaves unchanged
    assert apply_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="inconclusive",
        obligation_coverage=1.0, created_regressions=[], arms_differ=True,
        expected_status_version=version) is None
    status = get_status(conn, rule_id=rule_id, target_scope="drc")
    assert status["status"] == "shadow"
    assert status["status_version"] == version


def test_lifecycle_status_preserves_outer_transaction(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_valid_rule(tmp_tehm, sample_record_dict)
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("lifecycle-caller-sentinel", "pending"),
    )
    version = enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    assert version == 1
    assert conn.in_transaction is True
    conn.rollback()
    assert get_status(conn, rule_id=rule_id, target_scope="drc") is None
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='lifecycle-caller-sentinel'"
    ).fetchone() is None


def test_trial_writers_preserve_outer_transaction(tmp_tehm):
    conn, _, _ = tmp_tehm
    subject = TEHMRuleTrialSubject(rule_id="rule_tx", status_version=3)
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("trial-caller-sentinel", "pending"),
    )
    trial = run_trial(
        conn, subject=subject, context=RepairContext(check="drc"),
        arm_a_evaluator=lambda _plan, _ctx: {"success": False},
        arm_b_evaluator=lambda _plan, _ctx: {"success": True},
        repeats=2, trial_uuid="tx-run")
    external = record_external_trial(
        conn, rule_id="rule_tx", target_scope="drc", verdict="win",
        metrics={"source": "external"}, status_version=3,
        trial_uuid="tx-external", arm_a_run_id="a", arm_b_run_id="b")
    assert trial["verdict"] == "win"
    assert external["trial_id"] == "trial_tx-external"
    assert conn.in_transaction is True
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_trials WHERE trial_uuid IN (?, ?)",
        ("tx-run", "tx-external")).fetchone()[0] == 2
    conn.rollback()
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_trials WHERE trial_uuid IN (?, ?)",
        ("tx-run", "tx-external")).fetchone()[0] == 0
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='trial-caller-sentinel'"
    ).fetchone() is None


def test_external_trial_replay_is_idempotent_and_conflicts_rejected(tmp_tehm):
    conn, _, _ = tmp_tehm
    kwargs = {
        "rule_id": "rule_replay",
        "target_scope": "drc",
        "verdict": "win",
        "metrics": {"source": "external", "samples": [0.0, 1.0]},
        "status_version": 4,
        "trial_uuid": "replay-external",
        "arm_a_run_id": "a-1",
        "arm_b_run_id": "b-1",
    }
    first = record_external_trial(conn, **kwargs)
    second = record_external_trial(conn, **kwargs)
    assert second == first
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_trials WHERE trial_uuid=?",
        (kwargs["trial_uuid"],)).fetchone()[0] == 1

    with pytest.raises(ValueError, match="replay conflicts"):
        record_external_trial(
            conn, **{**kwargs, "metrics": {"source": "tampered"}})
    with pytest.raises(ValueError, match="replay conflicts"):
        record_external_trial(
            conn, **{**kwargs, "verdict": "loss"})
    assert conn.execute(
        "SELECT verdict, metrics_json FROM tehm_trials WHERE trial_uuid=?",
        (kwargs["trial_uuid"],)).fetchone()["verdict"] == "win"


def test_run_trial_replay_is_idempotent_and_conflicts_rejected(tmp_tehm):
    conn, _, _ = tmp_tehm
    subject = TEHMRuleTrialSubject(rule_id="rule_run_replay", status_version=2)
    context = RepairContext(check="drc")

    def control(_plan, _ctx):
        return {"success": False}

    def winning_rule(_plan, _ctx):
        return {"success": True}

    first = run_trial(
        conn, subject=subject, context=context,
        arm_a_evaluator=control, arm_b_evaluator=winning_rule,
        repeats=2, trial_uuid="replay-run")
    second = run_trial(
        conn, subject=subject, context=context,
        arm_a_evaluator=control, arm_b_evaluator=winning_rule,
        repeats=2, trial_uuid="replay-run")
    assert second == first
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_trials WHERE trial_uuid='replay-run'"
    ).fetchone()[0] == 1

    with pytest.raises(ValueError, match="replay conflicts"):
        run_trial(
            conn, subject=subject, context=context,
            arm_a_evaluator=lambda _plan, _ctx: {"success": True},
            arm_b_evaluator=winning_rule, repeats=2,
            trial_uuid="replay-run")


def test_uuidless_run_trial_replay_is_immutable(tmp_tehm):
    conn, _, _ = tmp_tehm
    subject = TEHMRuleTrialSubject(rule_id="rule_uuidless", status_version=1)
    context = RepairContext(check="drc")
    control = lambda _plan, _ctx: {"success": False}
    winner = lambda _plan, _ctx: {"success": True}

    first = run_trial(conn, subject=subject, context=context,
                      arm_a_evaluator=control, arm_b_evaluator=winner,
                      repeats=2)
    second = run_trial(conn, subject=subject, context=context,
                       arm_a_evaluator=control, arm_b_evaluator=winner,
                       repeats=2)
    assert second == first
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_trials WHERE trial_id=?",
        ("trial:rule_uuidless",)).fetchone()[0] == 1

    with pytest.raises(ValueError, match="replay conflicts"):
        run_trial(conn, subject=subject, context=context,
                  arm_a_evaluator=winner, arm_b_evaluator=winner,
                  repeats=2)
