"""Real-command TEHM ORFS A/B executor + rollback authority."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tehm import db as tehm_db
from tehm.ids import rule_id as mint_rule_id, stable_dumps
from tehm.honesty import h10_rollback_authority
from tehm.lifecycle.orfs_trial import (
    _infrastructure_failures,
    _strict_authority_from_metrics,
    reconcile_route_trial_evidence,
    run_pending_orfs_trials,
)
from tehm.lifecycle.rule_authority import verify_rule_authority
from tehm.lifecycle.rule_status import enter_shadow, get_status, set_status


def _insert_rule(conn, rule_id="rule_orfs_real", scope="drc"):
    before = {"type": "CONFIG_REWRITE", "target_check": scope,
              "knob": "CORE_UTILIZATION"}
    after = {"rewrite.value": "20", "execution.rerun_from": "floorplan",
             "execution.recheck": scope}
    if rule_id == "rule_orfs_real":
        rule_id = mint_rule_id(
            domain="flow.signoff", before_pattern=before,
            after_pattern=after, hard_preconditions=[],
            obligations=["TARGET_FAILURE_REMOVED", "PRESERVE_LVS"])
    now = tehm_db.now_local()
    conn.execute(
        """INSERT INTO tehm_rules (
               rule_id, domain, before_pattern_json, after_pattern_json,
               hard_preconditions_json, context_profile_json, obligations_json,
               validity_status, validity_profile_json, confidence_json,
               utility_json, risk_profile_json, predicate_schema_version,
               role_schema_version, crystallizer_version, merge_trace_digest,
               created_at, updated_at)
           VALUES (?, 'flow.signoff', ?, ?, '[]', '{}', ?, 'VALIDATED',
                   '{}', '{}', '{}', '[]', 'predicate-v0.1', 'role-v0.1',
                   'test', 'test', ?, ?)""",
        (rule_id, stable_dumps(before), stable_dumps(after),
         stable_dumps(["TARGET_FAILURE_REMOVED", "PRESERVE_LVS"]), now, now))
    conn.commit()
    enter_shadow(conn, rule_id=rule_id, target_scope=scope)
    set_status(conn, rule_id=rule_id, target_scope=scope, status="candidate")
    return rule_id


def _project(tmp_path):
    project = tmp_path / "subject"
    (project / "constraints").mkdir(parents=True)
    (project / "rtl").mkdir()
    (project / "reports").mkdir()
    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = subject\nexport PLATFORM = nangate45\n"
        "export CORE_UTILIZATION = 30\n")
    (project / "rtl" / "subject.v").write_text(
        "module subject(input clk); endmodule\n")
    (project / "reports" / "drc.json").write_text(
        '{"status":"fail","total_violations":4}')
    (project / "reports" / "lvs.json").write_text('{"status":"clean"}')
    return project


def _scripts(tmp_path):
    flow = tmp_path / "run_flow.sh"
    flow.write_text("""#!/usr/bin/env bash
set -euo pipefail
p="$1"
mkdir -p "$p/backend/RUN_test/final" "$p/reports"
printf 'gds' > "$p/backend/RUN_test/final/6_final.gds"
if [[ "$(basename "$p")" == "arm_b" && -n "${SOURCE_TO_MUTATE:-}" ]]; then
  printf 'escaped mutation\n' > "$SOURCE_TO_MUTATE"
fi
exit 0
""")
    fix = tmp_path / "fix.sh"
    fix.write_text("""#!/usr/bin/env bash
set -euo pipefail
p="$1"
mkdir -p "$p/reports"
if grep -q 'CORE_UTILIZATION = 20' "$p/constraints/config.mk"; then
  printf '{"status":"clean","total_violations":0}' > "$p/reports/drc.json"
else
  printf '{"status":"fail","total_violations":4}' > "$p/reports/drc.json"
fi
if [[ "$(basename "$p")" == "arm_b" && "${BREAK_LVS:-0}" == "1" ]]; then
  printf '{"status":"fail"}' > "$p/reports/lvs.json"
else
  printf '{"status":"clean"}' > "$p/reports/lvs.json"
fi
printf '{"status":"complete"}' > "$p/reports/route.json"
printf '{"tier":"clean"}' > "$p/reports/timing_check.json"
exit 0
""")
    return flow, fix


def test_strict_authority_metadata_replay_fails_closed_on_weak_types():
    assert _strict_authority_from_metrics({
        "production_authority": False}) is False
    assert _strict_authority_from_metrics({
        "production_authority": True}) is True
    assert _strict_authority_from_metrics({
        "production_authority": "false"}) is True
    assert _strict_authority_from_metrics({
        "promotion_gate_inputs": "false"}) is True


def test_real_orfs_ab_promotes_and_records_rollback(tmp_tehm, tmp_path):
    conn, store, _ = tmp_tehm
    rule_id = _insert_rule(conn)
    project = _project(tmp_path)
    original = (project / "constraints" / "config.mk").read_bytes()
    flow, fix = _scripts(tmp_path)

    trials = run_pending_orfs_trials(
        conn, store,
        base_entries=[{"design": "subject", "project_path": str(project),
                       "platform": "nangate45", "kind": "normal"}],
        run_flow_script=flow, fix_signoff_script=fix,
        n_designs=1, repeats=2, work_root=tmp_path / "arms",
        env={"SOURCE_TO_MUTATE": str(project / "constraints" / "config.mk")})

    assert len(trials) == 1
    assert trials[0]["verdict"] == "win"
    assert trials[0]["new_status"] == "promoted"
    assert (project / "constraints" / "config.mk").read_bytes() == original
    status = conn.execute(
        "SELECT status FROM tehm_rule_status WHERE rule_id=?", (rule_id,)
    ).fetchone()[0]
    assert status == "promoted"
    row = conn.execute(
        "SELECT metrics_json FROM tehm_trials WHERE rule_id=?", (rule_id,)
    ).fetchone()
    metrics = json.loads(row[0])
    assert metrics["arms_differ"] is True
    assert metrics["rollback_verified"] is True
    assert all(p["rollback_receipt"]["verified"] for p in metrics["pairs"])
    activations = conn.execute(
        "SELECT rollback_receipt_json, trial_uuid FROM tehm_activations "
        "WHERE rule_id=?", (rule_id,)).fetchall()
    assert len(activations) == 2
    assert all(json.loads(r[0])["verified"] for r in activations)
    assert all(r[1] for r in activations)
    assert h10_rollback_authority(conn)[0] is True


def test_strict_orfs_trial_projects_db_authority_and_ignores_gate_booleans(
        tmp_tehm, tmp_path):
    """Production mode must consume the trial projector, not gate booleans."""
    conn, store, _ = tmp_tehm
    rule_id = _insert_rule(conn)
    project = _project(tmp_path)
    flow, fix = _scripts(tmp_path)
    trials = run_pending_orfs_trials(
        conn, store,
        base_entries=[{"design": "subject", "project_path": str(project),
                       "platform": "nangate45", "kind": "normal"}],
        run_flow_script=flow, fix_signoff_script=fix,
        n_designs=1, repeats=2, work_root=tmp_path / "strict_arms",
        production_authority=True,
        # These values are deliberately forged diagnostics.  They must never
        # become authority evidence in the strict path.
        promotion_gate_inputs={rule_id: {
            "cross_lineage_te": 1.0, "harmful_rate": 0.0,
            "conformal_coverage": 1.0,
        }})

    assert len(trials) == 1
    assert trials[0]["verdict"] == "win"
    assert trials[0]["new_status"] is None
    assert get_status(conn, rule_id=rule_id, target_scope="drc")["status"] == (
        "candidate")
    metrics = json.loads(conn.execute(
        "SELECT metrics_json FROM tehm_trials WHERE rule_id=?", (rule_id,)
    ).fetchone()[0])
    authority = metrics["authority_receipt"]
    assert authority["eligible"] is False
    assert authority["gate_status"]["rollback_verified"] == "PASS"
    assert authority["gate_status"]["obligation_coverage"] == "PASS"
    assert authority["gate_status"]["registry_verified"] == "PASS"
    assert authority["gate_status"]["cross_lineage_te"] == "NOT_ESTABLISHED"
    assert authority["gate_status"]["harmful_rate"] == "NOT_ESTABLISHED"
    assert authority["gate_status"]["conformal_coverage"] == "NOT_ESTABLISHED"
    replay = verify_rule_authority(conn, authority)
    assert "trial_binding_mismatch" not in replay["reasons"]
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_rule_authority_receipts").fetchone()[0] == 1


def test_orfs_ab_serial_mode_preserves_trial_semantics(tmp_tehm, tmp_path,
                                                       monkeypatch):
    """Resource-bounded hosts may serialize the two otherwise isolated arms."""
    conn, store, _ = tmp_tehm
    rule_id = _insert_rule(conn)
    project = _project(tmp_path)
    flow, fix = _scripts(tmp_path)
    monkeypatch.setenv("R2G_ORFS_SERIAL_AB", "1")

    trials = run_pending_orfs_trials(
        conn, store,
        base_entries=[{"design": "subject", "project_path": str(project),
                       "platform": "nangate45", "kind": "normal"}],
        run_flow_script=flow, fix_signoff_script=fix,
        n_designs=1, repeats=1, work_root=tmp_path / "serial_arms")

    assert len(trials) == 1
    assert trials[0]["verdict"] == "win"
    assert trials[0]["new_status"] == "promoted"
    assert conn.execute(
        "SELECT status FROM tehm_rule_status WHERE rule_id=?", (rule_id,)
    ).fetchone()[0] == "promoted"


def test_orfs_ab_trial_is_idempotent(tmp_tehm, tmp_path):
    conn, store, _ = tmp_tehm
    _insert_rule(conn)
    project = _project(tmp_path)
    flow, fix = _scripts(tmp_path)
    kwargs = dict(
        base_entries=[{"design": "subject", "project_path": str(project),
                       "platform": "nangate45", "kind": "normal"}],
        run_flow_script=flow, fix_signoff_script=fix, repeats=2,
        work_root=tmp_path / "arms")
    first = run_pending_orfs_trials(conn, store, **kwargs)
    # Promoted rules leave the candidate queue, so no duplicate is produced.
    second = run_pending_orfs_trials(conn, store, **kwargs)
    assert len(first) == 1 and second == []
    assert conn.execute("SELECT COUNT(*) FROM tehm_trials").fetchone()[0] == 1


def test_promoted_rule_revalidation_records_ab_without_registry_mutation(
        tmp_tehm, tmp_path):
    conn, store, _ = tmp_tehm
    rule_id = _insert_rule(conn)
    set_status(conn, rule_id=rule_id, target_scope="drc", status="promoted")
    before = conn.execute(
        "SELECT status,status_version FROM tehm_rule_status WHERE rule_id=?",
        (rule_id,)).fetchone()
    project = _project(tmp_path)
    flow, fix = _scripts(tmp_path)
    kwargs = dict(
        base_entries=[{"design": "heldout", "project_path": str(project),
                       "platform": "nangate45", "kind": "normal"}],
        run_flow_script=flow, fix_signoff_script=fix, repeats=2,
        work_root=tmp_path / "revalidation_arms",
        lifecycle_statuses=frozenset({"promoted"}), mutate_lifecycle=False)
    trials = run_pending_orfs_trials(conn, store, **kwargs)
    assert len(trials) == 1
    assert trials[0]["new_status"] is None
    after = conn.execute(
        "SELECT status,status_version FROM tehm_rule_status WHERE rule_id=?",
        (rule_id,)).fetchone()
    assert tuple(after) == tuple(before) == ("promoted", 3)
    metrics = json.loads(conn.execute(
        "SELECT metrics_json FROM tehm_trials WHERE trial_uuid=?",
        (trials[0]["trial_uuid"],)).fetchone()[0])
    assert metrics["lifecycle_mode"] == "promoted_revalidation"
    assert metrics["registry_authority"]["mode"] == "revalidation_no_mutation"
    assert metrics["registry_authority"]["verified"] is True
    assert metrics["rollback_verified"] is True
    reused = run_pending_orfs_trials(conn, store, **kwargs)
    assert reused[0]["reused"] is True


def test_ab_infrastructure_classifier_is_narrow():
    infra = [{"arm_a": {"flow_stdout_tail":
                         "variables.mk: PLATFORM variable net set."},
              "arm_b": {"flow_stderr_tail": ""}}]
    assert _infrastructure_failures(infra) == [
        "hierarchical_design_config_not_staged"]
    design_failure = [{"arm_a": {"flow_stdout_tail":
                                  "ERROR DPL-0036 Detailed placement failed"},
                       "arm_b": {"flow_stderr_tail": "GRT-0116 congestion"}}]
    assert _infrastructure_failures(design_failure) == []


def test_regression_veto_keeps_registry_candidate_and_rollback_verified(
        tmp_tehm, tmp_path):
    conn, store, _ = tmp_tehm
    rule_id = _insert_rule(conn)
    project = _project(tmp_path)
    original = (project / "constraints" / "config.mk").read_bytes()
    flow, fix = _scripts(tmp_path)
    trials = run_pending_orfs_trials(
        conn, store,
        base_entries=[{"design": "subject", "project_path": str(project),
                       "platform": "nangate45", "kind": "normal"}],
        run_flow_script=flow, fix_signoff_script=fix, repeats=2,
        work_root=tmp_path / "arms", env={"BREAK_LVS": "1"})
    assert trials[0]["verdict"] == "win"  # target DRC improved...
    assert trials[0]["new_status"] is None  # ...but authority vetoed LVS regression
    assert conn.execute(
        "SELECT status FROM tehm_rule_status WHERE rule_id=?", (rule_id,)
    ).fetchone()[0] == "candidate"
    metrics = json.loads(conn.execute(
        "SELECT metrics_json FROM tehm_trials WHERE rule_id=?", (rule_id,)
    ).fetchone()[0])
    assert metrics["created_regressions"] == ["lvs"]
    assert metrics["rollback_verified"] is True
    assert (project / "constraints" / "config.mk").read_bytes() == original

    # Simulate a crash after trial evidence landed but before registry authority.
    metrics.pop("registry_authority")
    conn.execute("UPDATE tehm_trials SET metrics_json=? WHERE rule_id=?",
                 (stable_dumps(metrics), rule_id))
    conn.commit()
    replay = run_pending_orfs_trials(
        conn, store,
        base_entries=[{"design": "subject", "project_path": str(project),
                       "platform": "nangate45", "kind": "normal"}],
        run_flow_script=flow, fix_signoff_script=fix, repeats=2,
        work_root=tmp_path / "arms", env={"BREAK_LVS": "1"})
    assert replay[0]["reused"] is True
    repaired_metrics = json.loads(conn.execute(
        "SELECT metrics_json FROM tehm_trials WHERE rule_id=?", (rule_id,)
    ).fetchone()[0])
    assert repaired_metrics["registry_authority"]["verified"] is True


def test_route_evidence_reconciliation_replays_preserved_flow_logs(
        tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    rule_id = _insert_rule(conn, scope="route")
    pairs = []
    for repeat in range(2):
        sandbox = tmp_path / f"pair_{repeat}"
        for arm, status, final in (("arm_a", 1, None), ("arm_b", 0, 0)):
            run = sandbox / arm / "backend" / "RUN_test"
            reports = sandbox / arm / "reports"
            run.mkdir(parents=True)
            reports.mkdir(parents=True)
            (run / "stage_log.jsonl").write_text(
                json.dumps({"stage": "route", "status": status}) + "\n")
            text = "global routing failed\n" if final is None else (
                "[INFO DRT-0199] Number of violations = 35.\n"
                f"[INFO DRT-0199] Number of violations = {final}.\n")
            (run / "flow.log").write_text(text)
        activation_id = f"reconcile_{repeat}"
        conn.execute(
            """INSERT INTO tehm_activations (
              activation_id,rule_id,target_state_id,retrieval_receipt_json,
              applicability_status,binding_status,binding_json,executability_status,
              obligation_transfer_json,obligation_coverage,verification_status,
              verifier_json,outcome,created_regressions_json,rollback_receipt_json,
              trial_uuid,created_at)
              VALUES (?,?,'t','{}','APPLICABLE','BOUND','{}','EXECUTABLE',
                      '{"results":[]}',1.0,'FAIL','{}','FAIL','[]',?,'trial','now')""",
            (activation_id, rule_id, stable_dumps({"verified": True})))
        pairs.append({
            "arm_a": {"flow_rc": 2, "success": False, "reports": {}},
            "arm_b": {"flow_rc": 0, "success": False, "reports": {}},
            "arms_differ": True, "created_regressions": [],
            "obligation_coverage": 1.0, "activation_id": activation_id,
            "rollback_receipt": {"verified": True,
                                  "sandbox_root": str(sandbox)},
        })
    metrics = {"pairs": pairs, "A_samples": [0.0, 0.0],
               "B_samples": [0.0, 0.0], "rollback_verified": True}
    conn.execute(
        "INSERT INTO tehm_trials VALUES "
        "('trial_trial',?,'route',NULL,NULL,'inconclusive',?,"
        "NULL,'trial',2,'now')", (rule_id, stable_dumps(metrics)))
    conn.commit()

    extractor = (Path(__file__).resolve().parents[2] /
                 "r2g-skills/signoff-loop/scripts/extract/extract_route.py")
    result = reconcile_route_trial_evidence(
        conn, trial_uuid="trial", extract_route_script=extractor)
    assert result["verdict"] == "win"
    assert result["new_status"] == "promoted"
    assert result["metrics"]["B_samples"] == [1.0, 1.0]
    assert conn.execute(
        "SELECT status FROM tehm_rule_status WHERE rule_id=?", (rule_id,)
    ).fetchone()[0] == "promoted"
    assert {r[0] for r in conn.execute(
        "SELECT outcome FROM tehm_activations WHERE trial_uuid='trial'")} == {"PASS"}


def test_engineer_loop_tehm_ab_drain_uses_real_executor_only(
        tmp_tehm, tmp_path, monkeypatch):
    conn, _, root = tmp_tehm
    _insert_rule(conn)
    project = _project(tmp_path)
    flow, fix = _scripts(tmp_path)
    repo = Path(__file__).resolve().parents[2]
    loop_dir = repo / "r2g-skills" / "signoff-loop" / "scripts" / "loop"
    if str(loop_dir) not in sys.path:
        sys.path.insert(0, str(loop_dir))
    import engineer_loop
    import knowledge_db

    # This fixture inserts an isolated rule row rather than a canonical source
    # corpus, so a rebuild honesty audit correctly reports it as unhealthy.
    # The seam under test is the real TEHM A/B executor and legacy-DB firewall.
    monkeypatch.setattr(
        engineer_loop, "_backend_learn_cycle",
        lambda backend: {"backend": backend.name, "ok": True})

    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    monkeypatch.setenv("TEHM_DB", str(root / "tehm.sqlite"))
    monkeypatch.setenv("TEHM_ARTIFACTS_ROOT", str(root / "artifacts"))
    monkeypatch.setenv("R2G_LOOP_RUN_FLOW", str(flow))
    monkeypatch.setenv("R2G_LOOP_FIX", str(fix))
    monkeypatch.setenv("R2G_TEHM_AB_REPEATS", "2")
    monkeypatch.setattr(
        knowledge_db, "connect",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("legacy DB opened by TEHM ab-drain")))
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = engineer_loop.Ledger(ledger_path)
    ledger.add({"design": "subject", "project_path": str(project),
                "platform": "nangate45", "kind": "normal"})
    judged = engineer_loop.ab_drain(ledger_path, n_ab_designs=1)
    assert judged == 1
    assert conn.execute("SELECT COUNT(*) FROM tehm_trials").fetchone()[0] == 1
    # The production backend has no independent cross-lineage/harmful/conformal
    # cohort in this fixture, so the strict promotion authority must fail closed.
    assert conn.execute(
        "SELECT status FROM tehm_rule_status").fetchone()[0] == "candidate"
    metrics = json.loads(conn.execute(
        "SELECT metrics_json FROM tehm_trials").fetchone()[0])
    assert metrics["promotion_gates"]["eligible"] is False
    assert set(metrics["promotion_gates"]["missing"]) == {
        "cross_lineage_te", "harmful_rate", "conformal_coverage"}
