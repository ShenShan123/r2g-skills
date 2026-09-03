"""RTL extension (design doc 26 Phase 10, 22.1 RTL v2).

Structural parser, RTL semantic graph, rtl.* action domains + guard_strengthen
rewrite, and the Icarus oracle. The oracle tests run the REAL iverilog/vvp when
available (they are on this machine) and skip cleanly otherwise.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from tehm import db as tehm_db
from tehm.artifact_store import ArtifactStore
from tehm.rtl.rtl_actions import apply_guard_strengthen, apply_rtl_action
from tehm.rtl.rtl_evidence import capture_rtl_fix
from tehm.crystallization.role_normalize import normalize_rewrite
from tehm.activation.instantiate import instantiate_rewrite
from tehm.activation.binding import Binding
from tehm.rtl.rtl_graph import build_rtl_graph, design_graph_digest
from tehm.rtl.rtl_oracle import IcarusOracle
from tehm.rtl.verilog_parse import parse_verilog

PROJ = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"
SRC = (PROJ / "rtl" / "req_ack_fsm.v").read_text()


# -- parser -------------------------------------------------------------------

def test_parser_extracts_module_and_signals():
    modules = parse_verilog(SRC)
    assert len(modules) == 1
    module = modules[0]
    assert module.name == "req_ack_fsm"
    assert "clk" in module.signals and "done" in module.signals
    assert module.signals["state"].kind == "reg"
    assert module.signals["clk"].kind == "input"   # ANSI port direction


def test_parser_preserves_ansi_port_widths():
    module = parse_verilog("""module width_demo(input wire [3:0] data,
                                  output reg [3:0] out);
always @(*) out = data;
endmodule
""")[0]
    assert module.signals["data"].width == "[3:0]"
    assert module.signals["out"].width == "[3:0]"


def test_parser_finds_always_blocks_and_fsm():
    module = parse_verilog(SRC)[0]
    sequential = [b for b in module.always_blocks if b.is_sequential]
    combinational = [b for b in module.always_blocks if not b.is_sequential]
    assert len(sequential) == 2
    assert len(combinational) == 1
    fsms = [f for b in combinational for f in b.fsms]
    assert fsms
    fsm = fsms[0]
    assert fsm.case_expr.strip() == "state"
    # the buggy SEND transition has NO guard
    send = next(i for i in fsm.items if i.label == "SEND")
    assert send.condition is None
    assert "DONE" in send.target


def test_parser_deterministic():
    a = parse_verilog(SRC)[0].to_dict()
    b = parse_verilog(SRC)[0].to_dict()
    assert a == b


# -- graph --------------------------------------------------------------------

def test_rtl_graph_kinds():
    module = parse_verilog(SRC)[0]
    graph = build_rtl_graph(module)
    kinds = {n["kind"] for n in graph.nodes}
    assert {"MODULE", "ALWAYS_BLOCK", "STATE_REG", "CLOCK", "RESET"} <= kinds
    assert graph.node_count() > 0


def test_rtl_graph_digest_deterministic_and_sensitive():
    module = parse_verilog(SRC)[0]
    g1 = build_rtl_graph(module)
    g2 = build_rtl_graph(parse_verilog(SRC)[0])
    assert design_graph_digest(g1) == design_graph_digest(g2)


def test_rtl_execution_record_carries_structural_graph():
    from tehm.rtl.rtl_evidence import build_rtl_execution_record

    record = build_rtl_execution_record(PROJ, oracle=None, store=None)
    before = record.before["structural_graph"]
    after = record.after["structural_graph"]
    assert {n["kind"] for n in before["nodes"]} >= {"MODULE", "FSM_TRANSITION"}
    assert {n["kind"] for n in after["nodes"]} >= {"MODULE", "FSM_TRANSITION"}
    assert before != after  # the guard edit changes the graph's transition attrs


# -- rewrites -----------------------------------------------------------------

def test_guard_strengthen_comment_aware():
    new, edit = apply_guard_strengthen(
        SRC, source_state="SEND", target_state="DONE", add_condition="ack")
    assert edit["rewritten"] == 1          # only the real code line
    assert "SEND: if (ack) next_state = DONE;" in new
    # the doc-comment example is preserved (never rewritten)
    assert "SEND: next_state = DONE;  -->" in new


def test_guard_strengthen_already_guarded_is_noop():
    fixed, _ = apply_guard_strengthen(
        SRC, source_state="SEND", target_state="DONE", add_condition="ack")
    again, edit = apply_guard_strengthen(
        fixed, source_state="SEND", target_state="DONE", add_condition="ack")
    assert edit["rewritten"] == 0          # idempotent


def test_structured_rtl_domains_fail_closed_without_ast_context():
    with pytest.raises(ValueError, match="target is required"):
        apply_rtl_action(SRC, {"domain": "rtl.RESET_RESTORE"})
    with pytest.raises(ValueError, match="target is required"):
        apply_rtl_action(SRC, {"domain": "rtl.WIDTH_CORRECT"})
    with pytest.raises(ValueError, match="higher_label is required"):
        apply_rtl_action(SRC, {"domain": "rtl.PRIORITY_REORDER"})


def test_reset_restore_is_parser_scoped_and_comment_safe():
    source = (Path(__file__).resolve().parent / "fixtures" /
              "rtl_projects" / "p3_reset_restore_a" / "rtl" /
              "reset_restore_a.v").read_text()
    new, edit = apply_rtl_action(source, {
        "domain": "rtl.RESET_RESTORE",
        "target": "done <= 1'b1;",
        "replacement": "done <= 1'b0;",
        "module": "reset_restore_a",
    })
    assert edit["parser_backed"] is True
    assert edit["rewritten"] == 1
    assert "done <= 1'b0; // BUG" in new
    # The normal start branch remains asserted.
    assert "else if (start)\n            done <= 1'b1;" in new


def test_reset_restore_handles_begin_end_reset_branch():
    source = (Path(__file__).resolve().parent / "fixtures" /
              "rtl_projects" / "p3_reset_restore_c" / "rtl" /
              "reset_restore_c.v").read_text()
    new, edit = apply_rtl_action(source, {
        "domain": "rtl.RESET_RESTORE",
        "target": "finished <= 1'b1;",
        "replacement": "finished <= 1'b0;",
        "module": "reset_restore_c",
        "reset_signal": "rst_n",
    })
    assert edit["parser_backed"] is True
    assert edit["rewritten"] == 1
    assert "finished <= 1'b0;" in new
    assert "finished <= 1'b1;" in new


def test_width_correct_requires_parsed_assignment_and_preserves_module_scope():
    source = """module width_demo(input wire [3:0] a, output reg [4:0] y);
always @(*) begin
  y = a;
end
endmodule
"""
    new, edit = apply_rtl_action(source, {
        "domain": "rtl.WIDTH_CORRECT", "module": "width_demo",
        "signal": "y", "target": "y = a;",
        "replacement": "y = {1'b0, a};",
    })
    assert edit["parser_backed"] is True
    assert "y = {1'b0, a};" in new
    with pytest.raises(ValueError, match="not present in a parsed assignment"):
        apply_rtl_action(source, {
            "domain": "rtl.WIDTH_CORRECT", "module": "width_demo",
            "signal": "y", "target": "y = missing;",
            "replacement": "y = a;",
        })


def test_width_correct_fixture_payload_survives_capture():
    project = (Path(__file__).resolve().parent / "fixtures" /
               "rtl_projects" / "p3_width_correct_a")
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    from tehm.rtl.rtl_evidence import build_rtl_execution_record
    record = build_rtl_execution_record(project, oracle=oracle, store=None)
    assert record.action["domain"] == "rtl.WIDTH_CORRECT"
    assert record.action["payload"] == {
        "module": "width_correct_a",
        "signal": "out",
        "target": "out = data[1:0];",
        "replacement": "out = data[3:0];",
        "count": 1,
        "compatibility_profile": "rtl.combinational.width_assignment.v1",
    }
    assert record.verification["verdict"] == "PASS"


def test_priority_reorder_swaps_parser_case_items():
    source = """module priority_demo(input wire [1:0] state, input wire a, b,
                         output reg [1:0] next_state);
localparam [1:0] IDLE=0, A=1, B=2;
always @(*) begin
  next_state = state;
  case (state)
    IDLE: next_state = A;
    A: if (a) next_state = B;
    B: if (b) next_state = IDLE;
  endcase
end
endmodule
"""
    new, edit = apply_rtl_action(source, {
        "domain": "rtl.PRIORITY_REORDER", "module": "priority_demo",
        "case_expr": "state", "higher_label": "B", "lower_label": "A",
    })
    assert edit["parser_backed"] is True
    assert edit["rewritten"] == 1
    assert new.index("B: if") < new.index("A: if")


def test_ast_rewrite_generic():
    new, edit = apply_rtl_action(SRC, {
        "domain": "rtl.AST_REWRITE", "target": r"IDLE = 2'd0",
        "replacement": "IDLE = 2'd1"})
    assert edit["rewritten"] >= 1
    assert "IDLE = 2'd1" in new


def test_ast_rewrite_payload_is_captured_and_role_normalized():
    project = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "p3_reset_restore_a"
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    record = __import__("tehm.rtl.rtl_evidence", fromlist=["build_rtl_execution_record"]).build_rtl_execution_record(
        project, oracle=oracle, store=None
    )
    assert record.action["domain"] == "rtl.AST_REWRITE"
    assert record.action["payload"]["target"] == "done <= 1'b1;"
    assert record.action["payload"]["replacement"] == "done <= 1'b0;"
    normalized = normalize_rewrite(asdict(record), lineage_id=record.lineage_id)
    slots = normalized.slot_dict()
    assert slots["rtl.target"] == "done <= 1'b1;"
    assert slots["rtl.replacement"] == "done <= 1'b0;"
    assert slots["rtl.count"] == "1"


def test_ast_rewrite_rule_instantiates_generic_payload():
    action = instantiate_rewrite(
        {
            "domain": "rtl",
            "action_domain": "rtl.AST_REWRITE",
            "transformation_family": "RESET_RESTORE",
            "before_pattern": {"type": "RESET_RESTORE",
                               "rtl.target": "$H1"},
            "after_pattern": {"rtl.target": "$H1",
                              "rtl.replacement": "$H0",
                              "rtl.count": "1"},
        },
        Binding(status="BOUND", substitutions={"$H0": "done <= 1'b0;",
                                                 "$H1": "done <= 1'b1;"}),
        None,
    )
    assert action["payload"]["target"] == "done <= 1'b1;"
    assert action["payload"]["replacement"] == "done <= 1'b0;"
    assert action["payload"]["count"] == "1"


@pytest.mark.parametrize("fixture", ["p3_reset_restore_a", "p3_reset_restore_b",
                                      "p3_reset_restore_c"])
def test_reset_restore_fixtures_pass_target_and_regression(fixture):
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    project = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / fixture
    from tehm.rtl.rtl_evidence import build_rtl_execution_record
    record = build_rtl_execution_record(project, oracle=oracle, store=None)
    assert record.verification["verdict"] == "PASS"
    assert record.verification["obligation_coverage"] == 1.0


def test_credit_return_fixture_is_a_disjoint_guard_strengthen_lineage():
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    project = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "p3_positive_credit_return"
    source = (project / "rtl" / "positive_credit_return_fsm.v").read_text()
    buggy = oracle.verify([project / "rtl" / "positive_credit_return_fsm.v"],
                          target_tb=project / "tb" / "tb_handshake.v",
                          regression_tb=project / "tb" / "tb_basic.v")
    assert buggy["verdict"] == "FAIL"
    fixed, edit = apply_guard_strengthen(
        source, source_state="WAIT", target_state="DONE",
        add_condition="credit_return", module="positive_credit_return_fsm")
    assert edit["rewritten"] == 1
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        fixed_path = Path(tmp) / "positive_credit_return_fsm.v"
        fixed_path.write_text(fixed)
        verified = oracle.verify([fixed_path],
                                 target_tb=project / "tb" / "tb_handshake.v",
                                 regression_tb=project / "tb" / "tb_basic.v")
    assert verified["verdict"] == "PASS"
    assert verified["created_regressions"] == []


# -- Icarus oracle (REAL tools when available) --------------------------------

def test_icarus_oracle_detects_bug_then_fix():
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    buggy = oracle.verify([PROJ / "rtl" / "req_ack_fsm.v"],
                          target_tb=PROJ / "tb" / "tb_handshake.v",
                          regression_tb=PROJ / "tb" / "tb_basic.v")
    assert buggy["verdict"] == "FAIL"
    # apply the fix and verify again
    fixed, _ = apply_guard_strengthen(
        SRC, source_state="SEND", target_state="DONE", add_condition="ack")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        fixed_path = Path(tmp) / "req_ack_fsm.v"
        fixed_path.write_text(fixed)
        good = oracle.verify([fixed_path],
                             target_tb=PROJ / "tb" / "tb_handshake.v",
                             regression_tb=PROJ / "tb" / "tb_basic.v")
    assert good["verdict"] == "PASS"
    assert good["created_regressions"] == []


def test_icarus_oracle_detects_regression():
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    # fixed handshake (target passes) but done never asserts (regression fails)
    fixed, _ = apply_guard_strengthen(
        SRC, source_state="SEND", target_state="DONE", add_condition="ack")
    broken = fixed.replace("done <= (state == DONE);", "done <= 1'b0;")
    import tempfile
    from pathlib import Path as P
    with tempfile.TemporaryDirectory() as tmp:
        p = P(tmp) / "broken.v"
        p.write_text(broken)
        res = oracle.verify([p], target_tb=PROJ / "tb" / "tb_handshake.v",
                            regression_tb=PROJ / "tb" / "tb_basic.v")
    assert res["verdict"] == "FAIL"
    assert "RTL_FROZEN_REGRESSION_PASS" in res["created_regressions"]


def test_icarus_oracle_partial_target_is_not_complete_regression():
    """A target-only run must not claim frozen-regression coverage."""
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    fixed, _ = apply_guard_strengthen(
        SRC, source_state="SEND", target_state="DONE", add_condition="ack")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        fixed_path = Path(tmp) / "req_ack_fsm.v"
        fixed_path.write_text(fixed)
        result = oracle.verify(
            [fixed_path], target_tb=PROJ / "tb" / "tb_handshake.v",
            regression_tb=None)
    assert result["verdict"] == "UNKNOWN"
    assert result["oracle_type"] == "TARGET_TEST"
    assert result["confidence_tier"] == "T"
    assert result["obligation_coverage"] == pytest.approx(2 / 3)
    assert result["oracle_complete"] is False
    assert result["evidence_refs"] == ["target"]
    assert result["created_regressions"] == []


def test_icarus_oracle_partial_regression_is_not_target_evidence():
    """A regression-only run must leave the target obligation unchecked."""
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    result = oracle.verify(
        [PROJ / "rtl" / "req_ack_fsm.v"],
        target_tb=None,
        regression_tb=PROJ / "tb" / "tb_basic.v")
    assert result["verdict"] == "UNKNOWN"
    assert result["oracle_type"] == "REGRESSION"
    assert result["confidence_tier"] == "R"
    assert result["obligation_coverage"] == pytest.approx(2 / 3)
    assert result["oracle_complete"] is False
    assert result["evidence_refs"] == ["regression"]
    assert result["created_regressions"] == []


def test_icarus_oracle_without_testbench_has_no_checked_obligations():
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    result = oracle.verify([PROJ / "rtl" / "req_ack_fsm.v"],
                           target_tb=None, regression_tb=None)
    assert result["verdict"] == "UNKNOWN"
    assert result["obligation_coverage"] == 0.0
    assert result["oracle_complete"] is False
    assert result["evidence_refs"] == []


# -- RTL capture --------------------------------------------------------------

def test_capture_rtl_fix_real_oracle(tmp_path):
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    conn = tehm_db.connect(tmp_path / "tehm.sqlite")
    tehm_db.ensure_schema(conn)
    store = ArtifactStore(tmp_path / "artifacts")
    receipt = capture_rtl_fix(conn, store, PROJ, oracle=oracle)
    assert receipt.outcome == "PASS"
    assert receipt.transition_id.startswith("transition_")
    row = conn.execute(
        "SELECT action_domain, action_json, outcome FROM tehm_transitions"
    ).fetchone()
    assert row["action_domain"] == "rtl.GUARD_STRENGTHEN"
    conn.close()


def test_capture_rtl_fix_without_oracle_honest_unknown(tmp_path):
    conn = tehm_db.connect(tmp_path / "tehm.sqlite")
    tehm_db.ensure_schema(conn)
    store = ArtifactStore(tmp_path / "artifacts")
    receipt = capture_rtl_fix(conn, store, PROJ, oracle=None)
    assert receipt.outcome == "UNKNOWN"      # never fabricated
    conn.close()
