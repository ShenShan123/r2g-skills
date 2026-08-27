#!/usr/bin/env python3
"""Dependency-free P0 integrity regression used by the v4 freeze replay."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def main() -> int:
    from tehm import db
    from tehm.activation.binding import bind_rule
    from tehm.activation.obligation_transfer import (
        finalize_obligations, transfer_obligations)
    from tehm.artifact_store import ArtifactStore
    from tehm.canonical.capture import ExecutionRecord, capture
    from contracts import RepairContext
    from tehm.crystallization.build_rules import crystallize_all
    from tehm.dataset import assign_transition
    from tehm.lifecycle.authority import apply_trial_verdict
    from tehm.lifecycle.rule_status import enter_shadow, get_status
    from tehm.rtl.rtl_actions import apply_rtl_action

    root = Path(__file__).resolve().parents[1]
    reset_source = (root / "tests" / "fixtures" / "rtl_projects" /
                    "p3_reset_restore_a" / "rtl" /
                    "reset_restore_a.v").read_text()
    reset_fixed, reset_edit = apply_rtl_action(reset_source, {
        "domain": "rtl.RESET_RESTORE", "module": "reset_restore_a",
        "target": "done <= 1'b1;", "replacement": "done <= 1'b0;",
    })
    assert reset_edit["parser_backed"] and reset_edit["rewritten"] == 1
    assert "done <= 1'b0;" in reset_fixed
    width_source = ("module width_demo(input wire [3:0] a, output reg [4:0] y);\n"
                    "always @(*) begin y = a; end\nendmodule\n")
    width_fixed, width_edit = apply_rtl_action(width_source, {
        "domain": "rtl.WIDTH_CORRECT", "module": "width_demo",
        "signal": "y", "target": "y = a;",
        "replacement": "y = {1'b0, a};",
    })
    assert width_edit["parser_backed"] and "{1'b0, a}" in width_fixed
    priority_source = ("module priority_demo(input wire [1:0] state, input wire a, b,\n"
                       " output reg [1:0] next_state);\n"
                       "always @(*) begin\n next_state = state;\n"
                       " case (state)\n  A: if (a) next_state = B;\n"
                       "  B: if (b) next_state = A;\n endcase\nend\nendmodule\n")
    priority_fixed, priority_edit = apply_rtl_action(priority_source, {
        "domain": "rtl.PRIORITY_REORDER", "module": "priority_demo",
        "case_expr": "state", "higher_label": "B", "lower_label": "A",
    })
    assert priority_edit["parser_backed"]
    assert priority_fixed.index("B: if") < priority_fixed.index("A: if")
    sample = json.loads(
        (root / "tests" / "fixtures" / "sample_antenna_fix_record.json").read_text())
    with tempfile.TemporaryDirectory(prefix="tehm-p0-smoke-") as td:
        conn = db.connect(Path(td) / "tehm.sqlite")
        db.ensure_schema(conn)
        store = ArtifactStore(Path(td) / "artifacts")
        for i in range(3):
            record = json.loads(json.dumps(sample))
            record.update({
                "record_id": f"p0-smoke-{i}",
                "lineage_id": f"p0-smoke-lineage-{i}",
                "design_id": f"p0-smoke-design-{i}",
                "episode": {"episode_id": f"p0-smoke-episode-{i}",
                            "lineage_id": f"p0-smoke-lineage-{i}",
                            "step_index": 0,
                            "terminal_status": "VERIFIED_REPAIR"},
            })
            record["action"]["payload"]["config_edits"] = {
                "PLACE_DENSITY_LB_ADDON": f"0.1{i + 4}"}
            record["before"]["config"]["PLACE_DENSITY_LB_ADDON"] = "0.10"
            record["after"]["config"]["PLACE_DENSITY_LB_ADDON"] = f"0.1{i + 4}"
            record["observation_delta"]["first_divergence"]["before"] = 10 + i
            capture(conn, store, ExecutionRecord.from_dict(record))

        rules = crystallize_all(conn)
        assert len(rules) == 1, rules
        rule_id = rules[0]["rule_id"]
        sources = conn.execute(
            "SELECT episode_id, lineage_id, source_substitution_json "
            "FROM tehm_rule_sources WHERE rule_id=?", (rule_id,)).fetchall()
        assert {row["lineage_id"] for row in sources} == {
            "p0-smoke-lineage-0", "p0-smoke-lineage-1", "p0-smoke-lineage-2"}
        for row in sources:
            owned = {item[0] for item in conn.execute(
                "SELECT transition_id FROM tehm_episode_steps WHERE episode_id=?",
                (row["episode_id"],))}
            assert set(json.loads(row["source_substitution_json"])) <= owned

        enter_shadow(conn, rule_id=rule_id, target_scope="drc")
        conn.execute(
            "UPDATE tehm_rules SET utility_json=? WHERE rule_id=?",
            ('{"activations":7,"positive":6,"neutral":1,"harmful":0}', rule_id))
        conn.commit()
        crystallize_all(conn)
        utility = json.loads(conn.execute(
            "SELECT utility_json FROM tehm_rules WHERE rule_id=?", (rule_id,)
        ).fetchone()[0])
        assert utility["activations"] == 7
        for row in conn.execute("SELECT transition_id FROM tehm_transitions").fetchall():
            assign_transition(conn, transition_id=row[0], campaign_id="live",
                              split="heldout", learner_eligible=False)
        assert crystallize_all(conn) == []
        assert get_status(conn, rule_id=rule_id,
                          target_scope="drc")["status"] == "retired"

        context = type("Context", (), {"reports": {}, "cfg": {"LOCAL": "yes"}})()
        transfer = transfer_obligations(
            {"obligations": ["TARGET_FAILURE_REMOVED"]}, context)
        assert transfer["results"][0]["status"] == "SYNTHESIZABLE"
        assert transfer["obligation_coverage"] == 0.0
        finalized = finalize_obligations(transfer, {
            "verdict": "PASS", "obligation_coverage": 1.0,
            "evidence_refs": ["oracle:test"], "oracle_type": "TEST",
        })
        assert finalized["results"][0]["status"] == "PASS"
        assert finalized["results"][0]["evidence_refs"] == ["oracle:test"]

        binding = bind_rule(
            {"before_pattern": {"target_check": "rtl"},
             "after_pattern": {"rtl.target_state": "$H0"}},
            RepairContext(check="rtl", structural_graph={
                "nodes": [{"id": "fsm", "kind": "FSM_TRANSITION",
                           "target_state": "DONE"}], "edges": []}),
        )
        assert binding.status == "BOUND"
        assert binding.proof["resolution"]["$H0"]["source"] == "structural_graph"

        version = get_status(conn, rule_id=rule_id,
                             target_scope="drc")["status_version"]
        assert apply_trial_verdict(
            conn, rule_id=rule_id, target_scope="drc", verdict="win",
            obligation_coverage=None, created_regressions=[], arms_differ=True,
            expected_status_version=version) is None
        conn.close()
    print("P0 regression smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
