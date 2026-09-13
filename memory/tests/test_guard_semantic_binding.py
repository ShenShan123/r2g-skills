"""Source-only locator, contract replay, and answer firewall conformance."""
import copy
from pathlib import Path

import pytest

from tehm.assets.guard_binding import (
    locate_guard_conjunction, with_guard_conjunction_binding, verify_guard_binding,
    PROFILE, DOMAIN,
)
from tehm.assets.structural_binding import bind_rtl_asset_to_source, _shape
from tehm.assets.lifecycle import _binding_is_compatible
from tehm.assets.synthesis import build_rtl_asset_proposal
from tehm.rtl.rtl_actions import apply_rtl_action

SOURCE = """module worker(input clk, input start, input send, input grant,
 input [15:0] data, output reg [15:0] value, output finished);
reg [1:0] phase, upcoming;
localparam IDLE=0, WAIT=1, DONE=2;
wire accepted = send && grant;
assign finished = phase == DONE;
always @(posedge clk) begin
 phase <= upcoming;
 if(phase == WAIT && accepted) value <= data;
end
always @(*) begin
 upcoming=phase;
 case(phase)
  IDLE: if(start) upcoming=WAIT;
  WAIT: if(send) upcoming=DONE;
  DONE: upcoming=IDLE;
  default: upcoming=IDLE;
 endcase
end
endmodule
"""


def _asset():
    located = locate_guard_conjunction(SOURCE)
    proposal = build_rtl_asset_proposal(
        {"gap_id": "fixture-gap", "evidence_transitions": []}, name="guard-conjoin",
        transformation_family="ACCEPTANCE_COMPLETENESS",
        action_payload_template=located["payload"], compatibility_profile=PROFILE,
        verifier_obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS"))
    return with_guard_conjunction_binding(proposal, SOURCE).to_dict()


def test_source_only_symbol_roles_not_port_names():
    located = locate_guard_conjunction(SOURCE)
    assert located["payload"]["add_condition"] == "grant"
    assert located["payload"]["source_state"] == "WAIT"
    assert located["spec"]["proof_scope"] == "syntactic_not_functional"
    source = SOURCE.replace("send", "a").replace("grant", "b").replace("finished", "terminal")
    assert locate_guard_conjunction(source)["payload"]["add_condition"] == "b"


def test_non_alpha_datapath_and_fsm_prefix_transfer():
    target = SOURCE.replace("[15:0] value", "[31:0] value")
    target = target.replace("value <= data", "value <= value + data")
    target = target.replace("IDLE=0, WAIT=1, DONE=2", "IDLE=0, PREP=1, WAIT=2, DONE=3")
    target = target.replace("if(start) upcoming=WAIT;", "if(start) upcoming=PREP;\n  PREP: upcoming=WAIT;")
    assert _shape(target)[0] != _shape(SOURCE)[0]
    asset = _asset()
    bound = bind_rtl_asset_to_source(asset, target, design_id="target")
    assert verify_guard_binding(bound, asset)
    assert _binding_is_compatible(bound, asset)
    new, edit = apply_rtl_action(target, bound["definition"]["action"]["payload"])
    assert edit["rewritten"] == 1 and "if(send && grant)" in new


def test_binding_reads_no_files_and_ignores_answer_comments(monkeypatch):
    asset = _asset()
    def reject(*args, **kwargs):
        raise AssertionError("binding must consume source only")
    monkeypatch.setattr(Path, "read_text", reject)
    first = bind_rtl_asset_to_source(asset, SOURCE, design_id="target")
    second = bind_rtl_asset_to_source(asset, SOURCE + "\n// FIX: use wrong_signal", design_id="target")
    assert first == second


@pytest.mark.parametrize("change", [
    ("send && grant", "send || grant"),
    ("send && grant", "send && send"),
    ("if(send)", "if(send && grant)"),
    ("phase == WAIT && accepted", "phase == IDLE && accepted"),
    ("phase == WAIT && accepted", "phase == WAIT || accepted"),
    ("value <= data", "upcoming <= data"),
    ("assign finished = phase == DONE", "assign finished = phase != DONE"),
    ("input grant", "input [1:0] grant"),
    ("posedge clk", "negedge clk"),
    ("phase <= upcoming", "phase <= IDLE"),
    ("DONE: upcoming=IDLE", "DONE: if(send) upcoming=IDLE"),
])
def test_wrong_or_missing_semantic_facts_fail_closed(change):
    with pytest.raises(ValueError):
        locate_guard_conjunction(SOURCE.replace(*change))


def test_multiple_acceptance_aliases_fail_closed():
    target = SOURCE.replace("wire accepted", "wire second = send && grant;\nwire accepted")
    target = target.replace("if(phase == WAIT && accepted)", "if(phase == WAIT && second) value <= data;\n if(phase == WAIT && accepted)")
    with pytest.raises(ValueError, match="ambiguous"):
        locate_guard_conjunction(target)


def test_grouped_state_acceptance_and_multi_write_true_branch():
    target = SOURCE.replace("phase == WAIT && accepted",
                            "(phase == IDLE || phase == WAIT) && accepted")
    assert locate_guard_conjunction(target)["payload"]["add_condition"] == "grant"
    target = SOURCE.replace("value <= data;", "begin value <= data; phase <= upcoming; end")
    assert locate_guard_conjunction(target)["payload"]["add_condition"] == "grant"


def test_nested_conditional_datapath_is_not_proof_of_acceptance():
    target = SOURCE.replace("value <= data;", "begin if(start) value <= data; end")
    with pytest.raises(ValueError):
        locate_guard_conjunction(target)


@pytest.mark.parametrize("contract", [None, [], {}, 1])
def test_malformed_contract_fails_closed_without_exception(contract):
    asset = _asset()
    bound = bind_rtl_asset_to_source(asset, SOURCE, design_id="target")
    bound["provenance"]["binding_contract"] = contract
    assert not _binding_is_compatible(bound, asset)


@pytest.mark.parametrize("field", ["source", "payload", "digest", "template", "contract", "profile"])
def test_replay_and_lifecycle_reject_tampering(field):
    asset = _asset()
    bound = bind_rtl_asset_to_source(asset, SOURCE, design_id="target")
    bad = copy.deepcopy(bound)
    if field == "source":
        bad["provenance"]["binding_evidence"]["source"] = SOURCE.replace("send && grant", "send || grant")
    elif field == "payload":
        bad["definition"]["action"]["payload"]["add_condition"] = "send"
    elif field == "digest":
        bad["provenance"]["binding_digest"] = "sha256:tampered"
    elif field == "template":
        bad["definition"]["binding_template"]["spec"]["locator"] = "caller_boolean"
    elif field == "contract":
        bad["provenance"]["binding_contract"] = "unregistered_contract"
    else:
        bad["definition"]["action"]["payload"]["compatibility_profile"] = "rtl.fsm.single_guard.v1"
    assert not verify_guard_binding(bad, asset)
    assert not _binding_is_compatible(bad, asset)


def test_training_template_cannot_use_manifest_answer_instead_of_locator():
    located = locate_guard_conjunction(SOURCE)
    wrong = dict(located["payload"], add_condition="send")
    proposal = build_rtl_asset_proposal({}, name="wrong", transformation_family="fixture",
        action_payload_template=wrong, compatibility_profile=PROFILE,
        verifier_obligations=("RTL_TARGET_TEST_PASS",))
    with pytest.raises(ValueError):
        with_guard_conjunction_binding(proposal, SOURCE)
