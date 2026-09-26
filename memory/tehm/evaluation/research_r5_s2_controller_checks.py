"""Offline synthetic checks only: no model, oracle or empirical Agent outcomes."""
from __future__ import annotations

from dataclasses import asdict
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from . import research_r5_s2_controller as c

SOURCES = {"rtl/unit.v": "module unit(input x, output y); assign y = x; endmodule\n"}


def proposal(**updates):
    value = {"schema": c.PROPOSAL_SCHEMA, "base_digest": c.source_digest(SOURCES),
             "action": "replace_sources", "edits": [{"path": "rtl/unit.v",
             "content": SOURCES["rtl/unit.v"].replace("y = x", "y = ~x")}]}
    value.update(updates)
    return json.dumps(value)


def private(verdict):
    return {"obligations": dict.fromkeys(c.OBLIGATIONS, verdict),
            "private_gold": "DO_NOT_FORWARD", "expected_values": [123],
            "test_path": "/private/reference.sv", "trace": "secret"}


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tehm-r5-s2-component-")
        self.root = Path(self.tmp.name)
        self.ledgers = []

    def tearDown(self):
        for ledger in self.ledgers:
            ledger.close()
        self.tmp.cleanup()

    def ledger(self, policy="tehm", **limits):
        config = dict(proposals=2, total_reserved_tokens=200,
                      max_input_tokens=80, max_output_tokens=20, evaluations=2)
        config.update(limits)
        obj = c.BudgetLedger(self.root/f"ledger-{len(self.ledgers)}.jsonl", policy, c.Limits(**config))
        self.ledgers.append(obj)
        return obj

    def ready(self, ledger):
        ledger.reserve_proposal(30)
        ledger.finish_proposal("RECEIVED", c.source_digest(SOURCES))
        ledger.reserve_evaluation(c.source_digest(SOURCES))

    def test_same_action_capability_all_policies(self):
        outcomes = []
        for policy in c.POLICIES:
            ledger = self.ledger(policy)
            ledger.reserve_proposal(30)
            candidate, receipt = c.candidate_from_proposal(SOURCES, proposal())
            ledger.finish_proposal("RECEIVED", receipt["after_digest"])
            ledger.reserve_evaluation(receipt["after_digest"])
            feedback = ledger.finish_evaluation(private("FAIL"))
            path = c.write_candidate(self.root, policy, candidate)
            self.assertEqual((path/"rtl/unit.v").read_text(), candidate["rtl/unit.v"])
            outcomes.append((candidate, receipt, feedback, asdict(ledger.limits), ledger.tokens))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[1], outcomes[2])
        self.assertEqual(SOURCES["rtl/unit.v"].count("~"), 0)

    def test_no_action_is_not_success(self):
        candidate, receipt = c.candidate_from_proposal(SOURCES, proposal(action="no_action", edits=[]))
        self.assertEqual(candidate, SOURCES)
        self.assertEqual(receipt["status"], "NO_CHANGE")
        self.assertEqual(receipt["functional_verdict"], "NOT_EVALUATED")
        with self.assertRaises(c.ControllerError):
            c.candidate_from_proposal(SOURCES, proposal(action="no_action"))

    def test_response_schema_and_identity(self):
        for raw in ["{}", "[]", "null", "broken", proposal(base_digest="0"*64),
                    proposal(verdict="PASS"), proposal(gold_path="/private/a.v"),
                    proposal(action="shell"), proposal(edits=[]),
                    proposal().replace('"action":', '"action":"no_action","action":')]:
            with self.subTest(raw=raw[:60]), self.assertRaises(c.ControllerError):
                c.candidate_from_proposal(SOURCES, raw)

    def test_reject_paths_and_duplicate_edits(self):
        for name in ["../a.v", "/tmp/a.v", ".git/a.v", "rtl//a.v", "rtl/./a.v",
                     "rtl/a.py", "rtl/back\\slash.v", "rtl/foreign.v", "rtl/a.v/../unit.v"]:
            with self.subTest(name=name), self.assertRaises(c.ControllerError):
                c.candidate_from_proposal(SOURCES, proposal(edits=[{"path": name, "content": "module x; endmodule"}]))
        edits = [{"path": "rtl/unit.v", "content": "module unit; endmodule"}]*2
        with self.assertRaises(c.ControllerError):
            c.candidate_from_proposal(SOURCES, proposal(edits=edits))

    def test_bounds_and_types(self):
        for content in [None, 0, "", "a\0b", "x"*(c.MAX_SOURCE_BYTES+1)]:
            with self.subTest(content_type=type(content)), self.assertRaises(c.ControllerError):
                c.candidate_from_proposal(SOURCES, proposal(edits=[{"path": "rtl/unit.v", "content": content}]))
        with self.assertRaises(c.ControllerError):
            c.candidate_from_proposal(SOURCES, "x"*(c.MAX_RESPONSE_BYTES+1))
        for value in [True, 0, -1, 1.5, "2"]:
            with self.subTest(value=value), self.assertRaises(c.ControllerError):
                self.ledger(proposals=value)

    def test_fresh_candidate_directory_and_links(self):
        c.write_candidate(self.root, "a", SOURCES)
        with self.assertRaises(FileExistsError):
            c.write_candidate(self.root, "a", SOURCES)
        linked=self.root/"linked"; linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(c.ControllerError):
            c.write_candidate(linked, "b", SOURCES)
        with self.assertRaises(c.ControllerError):
            c.write_candidate(self.root, "../escaped", SOURCES)

    def test_feedback_projection_and_all_verdict_combinations(self):
        for values in itertools.product(["PASS", "FAIL", "UNKNOWN"], repeat=3):
            result=private("PASS"); result["obligations"]=dict(zip(c.OBLIGATIONS, values))
            feedback=c.public_feedback(result)
            expected="FAIL" if "FAIL" in values else "UNKNOWN" if "UNKNOWN" in values else "PASS"
            self.assertEqual(feedback["task_verdict"], expected)
            self.assertEqual(feedback["stop"], expected != "FAIL")
            self.assertNotIn("DO_NOT_FORWARD", json.dumps(feedback))
            self.assertNotIn("/private", json.dumps(feedback))
            self.assertEqual(set(feedback), {"schema", "obligations", "task_verdict", "stop"})

    def test_missing_and_malformed_feedback_unknown(self):
        for raw in [None, {}, {"obligations":{}}, {"obligations":dict.fromkeys(c.OBLIGATIONS,True)},
                    {"obligations":{**dict.fromkeys(c.OBLIGATIONS,"PASS"), "extra":"PASS"}}]:
            feedback=c.public_feedback(raw)
            self.assertEqual(feedback["task_verdict"], "UNKNOWN")
            self.assertTrue(feedback["stop"])

    def test_proposal_budget_pending_and_no_refund(self):
        ledger=self.ledger(total_reserved_tokens=100)
        ledger.reserve_proposal(30)
        with self.assertRaises(c.ControllerError): ledger.reserve_proposal(1)
        ledger.finish_proposal("INVALID")
        self.assertEqual(ledger.tokens,50)
        with self.assertRaises(c.ControllerError): ledger.reserve_evaluation(c.source_digest(SOURCES))
        ledger.reserve_proposal(30); ledger.finish_proposal("INVALID")
        self.assertEqual(ledger.tokens,100)
        with self.assertRaises(c.ControllerError): ledger.reserve_proposal(1)

    def test_total_and_per_call_caps(self):
        ledger=self.ledger(total_reserved_tokens=49)
        for tokens in [True, 0, -1, 81, 30]:
            with self.subTest(tokens=tokens), self.assertRaises(c.ControllerError):
                ledger.reserve_proposal(tokens)
        self.assertEqual((ledger.proposals,ledger.tokens),(0,0))

    def test_timeout_and_error_stop(self):
        for terminal in ["TIMEOUT","ERROR"]:
            ledger=self.ledger(); ledger.reserve_proposal(30); ledger.finish_proposal(terminal)
            with self.assertRaises(c.ControllerError): ledger.reserve_proposal(30)
            with self.assertRaises(c.ControllerError): ledger.reserve_evaluation(c.source_digest(SOURCES))
            self.assertEqual(ledger.tokens,50)

    def test_evaluation_binding_and_order(self):
        ledger=self.ledger()
        with self.assertRaises(c.ControllerError): ledger.finish_proposal("RECEIVED","a"*64)
        with self.assertRaises(c.ControllerError): ledger.finish_evaluation(private("PASS"))
        ledger.reserve_proposal(30)
        with self.assertRaises(c.ControllerError): ledger.finish_proposal("RECEIVED")
        ledger.finish_proposal("RECEIVED",c.source_digest(SOURCES))
        with self.assertRaises(c.ControllerError): ledger.reserve_proposal(30)
        with self.assertRaises(c.ControllerError): ledger.reserve_evaluation("a"*64)
        ledger.reserve_evaluation(c.source_digest(SOURCES))
        with self.assertRaises(c.ControllerError): ledger.reserve_evaluation(c.source_digest(SOURCES))
        with self.assertRaises(c.ControllerError): ledger.reserve_proposal(30)
        ledger.finish_evaluation(private("FAIL"))
        with self.assertRaises(c.ControllerError): ledger.reserve_evaluation(c.source_digest(SOURCES))
        ledger.reserve_proposal(30)

    def test_pass_unknown_stop_fail_can_continue(self):
        for verdict in ["PASS","UNKNOWN","FAIL"]:
            ledger=self.ledger(); self.ready(ledger); ledger.finish_evaluation(private(verdict))
            if verdict=="FAIL": ledger.reserve_proposal(30)
            else:
                with self.assertRaises(c.ControllerError): ledger.reserve_proposal(30)

    def test_evaluation_cap(self):
        ledger=self.ledger(evaluations=1); self.ready(ledger); ledger.finish_evaluation(private("FAIL"))
        ledger.reserve_proposal(30); ledger.finish_proposal("RECEIVED",c.source_digest(SOURCES))
        with self.assertRaises(c.ControllerError): ledger.reserve_evaluation(c.source_digest(SOURCES))

    def test_ledger_durable_chain_no_reopen(self):
        ledger=self.ledger(); self.ready(ledger); ledger.finish_evaluation(private("FAIL"))
        path=self.root/"ledger-0.jsonl"
        records=[json.loads(line) for line in path.read_text().splitlines()]
        previous=None
        for index, record in enumerate(records):
            value=dict(record); digest=value.pop("digest")
            self.assertEqual(digest,c.sha(c.canonical(value)))
            self.assertEqual(record["sequence"],index)
            self.assertEqual(record["previous_digest"],previous)
            previous=digest
        self.assertEqual([r["event"] for r in records], ["init","reserve_proposal",
            "proposal_terminal","reserve_evaluation","evaluation_terminal"])
        with self.assertRaises(FileExistsError): c.BudgetLedger(path,"tehm",ledger.limits)


    def test_multifile_closure_rejects_prefixes_and_invalid_earlier_file(self):
        for sources in [
                {"rtl/a.v":None, "rtl/b.v":"module b; endmodule"},
                {"rtl/a.v":"module a; endmodule", "rtl/a.v/b.v":"module b; endmodule"},
                {"rtl/a.v":"\ud800"},
                {"rtl/a.v":"x"*140000,"rtl/b.v":"x"*140000}]:
            with self.subTest(names=list(sources)), self.assertRaises(c.ControllerError):
                c.source_digest(sources)
        sources={"rtl/a.v":"module a; endmodule", "rtl/b.v":"module b; endmodule"}
        digest=c.source_digest(sources)
        response=proposal(base_digest=digest,edits=[{"path":"rtl/a.v","content":"module a; wire x; endmodule"}])
        candidate,receipt=c.candidate_from_proposal(sources,response)
        self.assertEqual(candidate["rtl/b.v"],sources["rtl/b.v"])
        self.assertEqual(receipt["changed_files"],["rtl/a.v"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
