"""Versioned guard/frontend conformance; not empirical evidence."""

import pytest

from tehm.rtl.verilog_parse import parse_verilog
from tehm.rtl.guard_conjunction import apply_guard_conjunction
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.compatibility import profile_for_action

BASE = """module m(input valid, input ready, input other, output done);
reg [1:0] state, ns;
localparam IDLE=0, WAIT=1, DONE=2;
wire accept = valid && ready;
assign done = state == DONE;
always @(*) begin
 ns = state;
 case(state)
  IDLE: if(other) ns=WAIT;
  WAIT: if(valid) ns=DONE;
  DONE: ns=IDLE;
  default: ns=IDLE;
 endcase
end
endmodule
"""
ARGS = dict(module="m", case_expr="state", reg="ns", source_state="WAIT",
            target_state="DONE", add_condition="ready")


def test_independent_domain_and_profile_dispatch():
    payload = dict(ARGS, domain="rtl.FSM_GUARD_CONJOIN")
    assert profile_for_action(payload) == "rtl.fsm.guard_conjunction.v1"
    assert apply_rtl_action(BASE, payload) == apply_guard_conjunction(BASE, **ARGS)
    unchanged, old = apply_rtl_action(BASE, dict(domain="rtl.GUARD_STRENGTHEN",
                                                **ARGS))
    assert unchanged == BASE and old["rewritten"] == 0


def test_rhs_is_not_declaration_and_width_is_retained():
    m = parse_verilog(BASE)[0]
    assert m.signals["valid"].kind == m.signals["ready"].kind == "input"
    assert m.signals["accept"].kind == "wire"
    assert m.signals["state"].width == m.signals["ns"].width == "[1:0]"
    assert "DONE" not in m.signals


def test_function_and_task_symbols_are_not_module_signals():
    src = BASE.replace("wire accept", """function [15:0] update;
 input [15:0] ready;
 reg [15:0] local_reg;
 begin update=ready; end
endfunction
task consume;
 input valid;
 reg local_task;
 begin local_task=valid; end
endtask
wire accept""")
    signals = parse_verilog(src)[0].signals
    assert signals["ready"].kind == signals["valid"].kind == "input"
    assert signals["ready"].width is None
    assert "local_reg" not in signals and "local_task" not in signals


def test_non_ansi_direction_survives_reg_redeclaration():
    m = parse_verilog("module m(out); output [7:0] out; reg [7:0] out; endmodule")[0]
    assert m.signals["out"].kind == "output"
    assert m.signals["out"].width == "[7:0]"


def test_initializer_commas_do_not_declare_operands():
    m = parse_verilog(BASE.replace("wire accept = valid && ready;",
                                  "wire accept = f(valid, ready), copy = {valid,ready};"))[0]
    assert m.signals["copy"].kind == "wire"
    assert "f" not in m.signals
    assert m.signals["valid"].kind == m.signals["ready"].kind == "input"


def test_unique_guard_conjoin_and_idempotence():
    src, edit = apply_guard_conjunction(BASE, **ARGS)
    assert edit["rewritten"] == 1
    assert src == BASE.replace("if(valid) ns=DONE;", "if(valid && ready) ns=DONE;")
    again, second = apply_guard_conjunction(src, **ARGS)
    assert again == src and second["rewritten"] == 0
    assert second["status"] == "ALREADY_CONJOINED"


def test_other_module_and_comments_cannot_be_rewritten():
    other = BASE.replace("module m", "module other")
    comments = "/* module m; WAIT: if(valid) ns=DONE; endmodule */\n"
    src = comments + BASE + other
    new, edit = apply_guard_conjunction(src, **ARGS)
    assert edit["rewritten"] == 1
    assert new.startswith(comments) and new.endswith(other)
    assert new == comments + BASE.replace("if(valid) ns=DONE;", "if(valid && ready) ns=DONE;") + other


@pytest.mark.parametrize("guard", ["valid || other", "!valid", "valid == other",
                                   "(valid)", "accept", "valid && valid", "1'b1"])
def test_unsupported_guards_fail_closed(guard):
    with pytest.raises(ValueError):
        apply_guard_conjunction(BASE.replace("if(valid)", f"if({guard})"), **ARGS)


@pytest.mark.parametrize("transition", ["if(valid) ns=DONE; else ns=IDLE;",
                                        "if(valid) begin ns=DONE; end",
                                        "if(valid) ns=DONE; ns=IDLE;",
                                        "if(valid) ns<=DONE;"])
def test_branching_and_extra_writes_fail_closed(transition):
    with pytest.raises(ValueError):
        apply_guard_conjunction(BASE.replace("if(valid) ns=DONE;", transition), **ARGS)


@pytest.mark.parametrize("patch", [
    {"module": "absent"}, {"source_state": "IDLE"}, {"target_state": "absent"},
    {"add_condition": "accept"}, {"add_condition": "ready || other"},
    {"reg": "other"}, {"case_expr": "other"},
])
def test_invalid_coordinates_fail_closed(patch):
    with pytest.raises(ValueError):
        apply_guard_conjunction(BASE, **(ARGS | patch))


def test_duplicate_module_is_rejected():
    with pytest.raises(ValueError):
        apply_guard_conjunction(BASE + BASE, **ARGS)


def test_duplicate_label_is_rejected():
    with pytest.raises(ValueError):
        apply_guard_conjunction(BASE.replace("  DONE:", "  WAIT: if(valid) ns=DONE;\n  DONE:"), **ARGS)


def test_vector_input_is_rejected():
    with pytest.raises(ValueError):
        apply_guard_conjunction(BASE.replace("input ready", "input [1:0] ready"), **ARGS)


def test_missing_case_is_rejected():
    with pytest.raises(ValueError):
        apply_guard_conjunction(BASE.replace("case(state)", "case(ns)"), **ARGS)
