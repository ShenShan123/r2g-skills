"""Native lane contract tests; fixture choices are NOT learned causal evidence."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "adapters"), str(ROOT / "sources/rtlscout")]
from bridge import FullMemory
from hooks import HostObserver, attach_rtlscout
from local_reuse import ScoutReuse, source_edits
from native_evidence import ScoutEvidence
from tehm.ids import stable_dumps

GOOD = "module adder(input [7:0] a,b,output [7:0] sum);assign sum=a+b;endmodule"
BAD = "module adder(input [7:0] a,b,output [7:0] sum);assign sum=a-b;endmodule"


def fixture_choice(state, code):
    action = {"domain": "rtl.AST_REWRITE", "payload": {"config_edits": {"design.sv": code}}}
    return {"mode": "direct_reuse", "routing": {"decision": "APPLY"}, "context": "",
        "selected_ids": ["UNIT-TEST-FIXTURE-NOT-A-PRODUCTION-RULE"], "action": action,
        "direct_proposal": {"candidate_id": "UNIT-TEST-FIXTURE-NOT-A-PRODUCTION-RULE",
            "binding": {"status": "BOUND", "lifecycle_status": "promoted", "action": action,
                "proof": {"target_context_digest": hashlib.sha256(stable_dumps(state.context().to_dict()).encode()).hexdigest()}},
            "obligations": [{"obligation": "PRESERVE_FROZEN_REGRESSION", "status": "SYNTHESIZABLE"}]}}


def make_native(code):
    from rtlscout.agent import RTLAgent
    from rtlscout.fake_provider import build_fake_client
    root = Path(tempfile.mkdtemp(prefix="scout-local-lane-", dir=ROOT / "reports"))
    memory = FullMemory("rtlscout", root=root)
    agent = RTLAgent(build_fake_client("simple_adder_pass"), workdir=memory.arm_root / "episodes/fixture",
                     max_steps=3, language="verilog", run_cec=False)
    agent.design_top_module = "adder"
    shutil.copy2(ROOT / "sources/rtlscout/rtlscout/smoke_benchmarks/simple_adder/tb.sv", agent.workspace / "tb.sv")
    (agent.workspace / "design.sv").write_text(code)
    evidence = ScoutEvidence(memory, agent, "UNIT-TEST-ONLY")
    observer = HostObserver(memory, evidence.state, evidence.verify)
    attach_rtlscout(agent, observer)
    agent._execute_tool("run_evaluation", {"filename": "design.sv"})
    return memory, agent, evidence, observer


@pytest.mark.parametrize("success", [True, False])
def test_native_local_execution_and_verified_rollback_without_llm(success):
    memory, agent, evidence, observer = make_native(BAD if success else GOOD)
    try:
        entry = evidence.state()
        driver = ScoutReuse(agent, observer, evidence)
        choice = fixture_choice(entry, GOOD if success else BAD)
        outcome = driver(choice)
        assert outcome["accepted"] is success
        assert agent.all_evals[-1]["passed"] is True
        events = [json.loads(l) for l in memory.journal.path.read_text().splitlines()]
        writes = [e for e in events if e["event"] == "memory_write"]
        assert writes[-1]["rolled_back"] is (not success)
        if success:
            assert writes[-1]["outcome"] == "PASS" and len(agent.all_evals) == 2
        else:
            assert writes[-1]["outcome"] == "REGRESSION" and len(agent.all_evals) == 3
            assert evidence.state().config == entry.config
            assert len(writes) == 2  # recovery cannot become positive training evidence
            assert driver(choice) is None and len(agent.all_evals) == 3
    finally:
        memory.close()


@pytest.mark.parametrize("trap", ["wrong_state", "unpromoted", "unavailable_oracle", "test_edit", "wrong_action"])
def test_local_projection_refuses_invalid_or_unbound_edits(trap):
    from bridge import State
    state = State("fixture", "fixture", "rtl", "rtlscout:verilog", "functional_and_cost",
                  {"evaluation": {"status": "fail"}}, {"design.sv": BAD}, {})
    choice = fixture_choice(state, GOOD)
    if trap == "wrong_state": choice["direct_proposal"]["binding"]["proof"]["target_context_digest"] = "bad"
    elif trap == "unpromoted": choice["direct_proposal"]["binding"]["lifecycle_status"] = "candidate"
    elif trap == "unavailable_oracle": choice["direct_proposal"]["obligations"][0]["status"] = "UNAVAILABLE"
    elif trap == "test_edit": choice["action"]["payload"]["config_edits"]["tb.sv"] = GOOD
    elif trap == "wrong_action": choice["action"] = {"payload": {"config_edits": {"design.sv": "different"}}}
    with pytest.raises(ValueError): source_edits(choice, state, allowed_names={"design.sv"})


@pytest.mark.parametrize("decision", ["ABSTAIN", "INAPPLICABLE", "NO_SKILL"])
def test_retrieval_hit_cannot_bypass_the_existing_router_veto(decision):
    from types import SimpleNamespace as Obj
    from bridge import routed_proposals
    proposal = Obj(applicability_status="APPLICABLE", candidate_id="candidate",
                   binding={"status": "BOUND", "lifecycle_status": "promoted"})
    candidate = Obj(candidate_id="candidate", payload={"rule_id": "rule"})
    routing = Obj(decision=decision, memory_budget=0, selected_rule_ids=())
    assert routed_proposals(routing, [proposal], [candidate]) == ([], None)


def test_apply_cannot_execute_a_different_retrieved_rule_and_consider_is_advice_only():
    from types import SimpleNamespace as Obj
    from bridge import routed_proposals
    p = Obj(applicability_status="APPLICABLE", candidate_id="c", binding={"status": "BOUND", "lifecycle_status": "promoted"})
    c = Obj(candidate_id="c", payload={"rule_id": "r"})
    assert routed_proposals(Obj(decision="APPLY", memory_budget=1, selected_rule_ids=("other",)), [p], [c]) == ([], None)
    assert routed_proposals(Obj(decision="APPLY", memory_budget=1, selected_rule_ids=("r",)), [p], [c]) == ([p], p)
    assert routed_proposals(Obj(decision="CONSIDER", memory_budget=1, selected_rule_ids=("r",)), [p], [c]) == ([p], None)
