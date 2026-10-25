"""P1b: one evidence count for runtime admission and rebuild (memory/tehm/evidence.py).

Design memory/evaluation/r2g_P1b_test_design_20261008.md: T1-T7, T16 (D2 table), canonical units,
legacy positives, D1 flag. In-memory stores; no frozen store is touched.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tehm import evidence as ev  # noqa: E402

V = {"oracle_complete": True}
REPAIR = {"experiment_kind": "REPAIR"}


def _t(outcome, *, verified=True, before=4, after=2, rolled_back="false", regs=None, rec=None, kind="REPAIR"):
    delta = {"experiment_kind": kind, "first_divergence": {"before": before, "after": after},
             "created_regressions": regs or []}
    if rolled_back is not None:
        delta["rolled_back"] = rolled_back
    # the shape canonical capture stores on a transition: record_id + episode_id (= the session)
    prov = {"record_id": rec[1], "episode_id": rec[0], "source": "canonical-capture-v1"} if rec else {}
    return outcome, {"oracle_complete": verified}, delta, prov


def _store(rule_sources, transitions):
    """rule_sources: {rule_id: [[own transition ids per source row]]}; transitions: {tid: _t(...)}"""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE tehm_transitions (transition_id TEXT, outcome TEXT, verifier_json TEXT, "
              "observation_delta_json TEXT, provenance_json TEXT)")
    c.execute("CREATE TABLE tehm_rule_sources (rule_id TEXT, episode_id TEXT, source_substitution_json TEXT)")
    for tid, (o, v, d, p) in transitions.items():
        c.execute("INSERT INTO tehm_transitions VALUES (?,?,?,?,?)", (tid, o, json.dumps(v), json.dumps(d), json.dumps(p)))
    for rule, rows in rule_sources.items():
        for i, tids in enumerate(rows):
            c.execute("INSERT INTO tehm_rule_sources VALUES (?,?,?)", (rule, "ep%d" % i, json.dumps({t: {} for t in tids})))
    return c


def _ev(rule_sources, transitions, rule="r"):
    return ev.rule_evidence(_store(rule_sources, transitions), rule)


def test_t1_a_non_own_pass_in_the_episode_does_not_count():
    e = _ev({"r": [["own"]]}, {"own": _t("NEUTRAL"), "other": _t("PASS", after=0)})
    assert e["positive"] == 0 and not ev.admissible(e, "candidate")


def test_t2_the_failure_a_rule_repaired_does_not_count_against_it():
    e = _ev({"r": [["own"]]}, {"failure": _t("FAIL", after=4), "own": _t("PASS", after=0)})
    assert e["positive"] == 1 and e["harmful"] == 0 and ev.admissible(e, "candidate")


def test_t3_net_zero_own_evidence_is_not_admitted():
    e = _ev({"r": [["p", "x"]]}, {"p": _t("PASS", after=0), "x": _t("REGRESSION", regs=["route:0->9"])})
    assert (e["positive"], e["harmful"]) == (1, 1) and not ev.admissible(e, "candidate")


def test_t4_copies_of_one_attempt_count_once():
    # one PASS attempt re-ingested as two transitions (same record), plus one REGRESSION
    e = _ev({"r": [["p1", "x"], ["p2"]]},
            {"p1": _t("PASS", after=0, rec=("s", "r:1")), "p2": _t("PASS", after=0, rec=("s", "r:1")),
             "x": _t("REGRESSION", regs=["drc:2->5"], rec=("s", "r:2"))})
    assert e["units"] == 2 and e["copies"] == 3 and (e["positive"], e["harmful"]) == (1, 1)
    assert not ev.admissible(e, "candidate")


def test_copies_that_disagree_take_the_most_conservative_class():
    e = _ev({"r": [["a", "b"]]}, {"a": _t("PASS", after=0, rec=("s", "1")), "b": _t("UNKNOWN", rec=("s", "1"))})
    assert e["positive"] == 0 and e["no_evidence"] == 1


def test_t5_unknown_and_unverified_are_no_evidence_either_way():
    e = _ev({"r": [["u", "w"]]}, {"u": _t("UNKNOWN"), "w": _t("PASS", verified=False, after=0)})
    assert e["no_evidence"] == 2 and e["positive"] == e["harmful"] == 0
    assert not ev.admissible(e, "candidate")                     # observed, nothing positive


def test_t6_neutral_is_never_a_success():
    e = _ev({"r": [["n"]]}, {"n": _t("NEUTRAL")})
    assert e["no_clear"] == 1 and not ev.admissible(e, "candidate")


def test_t7_verified_partial_is_positive():
    e = _ev({"r": [["p"]]}, {"p": _t("PARTIAL")})
    assert e["positive"] == 1 and ev.admissible(e, "candidate")


def test_t16_the_d2_rollback_table():
    e = _ev({"r": [["reg", "budget", "noimp", "pass_rb"]]},
            {"reg": _t("REGRESSION", regs=["drc_target_regression:2->5"], rolled_back="true"),      # negative
             "budget": _t("UNKNOWN", verified=False, before=2, after=2, rolled_back="true"),       # UNKNOWN
             "noimp": _t("FAIL", before=4, after=4, rolled_back="true"),                           # no clear
             "pass_rb": _t("PASS", after=0, rolled_back="true")})                                  # not positive
    assert (e["harmful"], e["no_evidence"], e["no_clear"], e["rolled_back"], e["positive"]) == (1, 1, 1, 1, 0)


def test_a_fail_that_worsened_is_harmful_and_an_undiagnosed_failed_rerun_is_unknown():
    # user review 2026-10-09: a FAIL with no after-count is a failed rerun; negative only when diagnosed
    diagnosed = _t("REGRESSION", before=4, after=None, regs=["flow_rerun_failed:rc2:DPL-0036"])
    stage_diag = _t("FAIL", before=None, after=None)
    stage_diag[1]["rerun_failure"] = {"attribution": "action", "reason": "infeasible:GRT-0116"}
    stage_diag[2]["failing_tests"] = {"before": 1, "after": 1}
    legacy = _t("FAIL", before=None, after=None)                  # legacy route re-abort, cause not stored
    legacy[2]["failing_tests"] = {"before": 1, "after": 1}
    unk = _t("FAIL", before=4, after=None)
    unk[1]["rerun_failure"] = {"attribution": "unknown", "reason": "no_error_code"}
    e = _ev({"r": [["w", "d", "s", "l", "u"]]}, {"w": _t("FAIL", before=2, after=5), "d": diagnosed,
                                              "s": stage_diag, "l": legacy, "u": unk})
    assert (e["harmful"], e["no_clear"], e["no_evidence"]) == (2, 1, 2)
    window = _t("REGRESSION", before=4, after=None, regs=["flow_rerun_failed:rc2"])   # P1b-1 shape, no code
    assert ev.classify(*window[:3]) == "no_evidence"


def test_a_legacy_pass_or_partial_without_a_marker_is_not_positive_in_the_main_caliber(monkeypatch):
    # user review 2026-10-09: "not rolled back" must be recorded or recovered, never assumed
    for o in ("PASS", "PARTIAL"):
        for marker in (None, "UNKNOWN"):
            e = _ev({"r": [["p"]]}, {"p": _t(o, after=0, rolled_back=marker)})
            assert (e["positive"], e["rollback_unknown"]) == (0, 1)
            assert not ev.admissible(e, "candidate")                          # main caliber (default)
            assert ev.admissible(e, "candidate", caliber_name="history")      # separate history caliber
    monkeypatch.setenv("TEHM_EVIDENCE_CALIBER", "history")
    assert ev.admissible(_ev({"r": [["p"]]}, {"p": _t("PASS", after=0, rolled_back=None)}), "candidate")
    monkeypatch.setenv("TEHM_EVIDENCE_CALIBER", "loose")
    import pytest
    with pytest.raises(ValueError):
        ev.caliber()


def test_promoted_stays_admissible_and_d1_requires_a_verified_positive(monkeypatch):
    empty = _ev({"r": [[]]}, {})
    assert not empty["observed"]
    assert not ev.admissible(empty, "candidate")                               # D1 is the default
    monkeypatch.setenv("TEHM_D1_REQUIRE_VERIFIED_POSITIVE", "0")
    assert ev.admissible(empty, "candidate")                                   # pre-D1 rule, for evaluation
    monkeypatch.delenv("TEHM_D1_REQUIRE_VERIFIED_POSITIVE")
    bad = _ev({"r": [["x"]]}, {"x": _t("REGRESSION", regs=["a"])})
    assert ev.admissible(bad, "promoted")                                      # never revoked here


def test_t15_evidence_is_not_trigger_bound_catalogue_or_llm_sources_admit_a_rule():
    # the rule's only verified positive comes from a catalogue- or LLM-executed source transition
    for prov in (("cat_session", "r2g:cat_session:iter1:cell_pad_relief"), ("llm_session", "r2g:llm:iter1:llm_edit")):
        e = _ev({"r": [["t"]]}, {"t": _t("PASS", after=0, rec=prov)})
        assert ev.admissible(e, "candidate")


def test_d1_no_verified_positive_stays_out_whatever_else_it_has():
    for units in ({"u": _t("UNKNOWN")}, {"n": _t("NEUTRAL")}, {"b": _t("PASS", after=0, rolled_back="true")}):
        e = _ev({"r": [list(units)]}, units)
        assert e["positive"] == 0 and not ev.admissible(e, "candidate")
