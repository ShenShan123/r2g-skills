"""Phase R amendments R-A3/R-A4: memory must not take away what the no-memory baseline reaches.

F1 trial admission (net-positive evidence), F2 witness integrity (verified + same situation),
F3 sign-coherent median delta. The structural containment lives in fix_signoff.sh and is tested in
r2g-skills/signoff-loop/tests/test_baseline_repairs.py.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tehm_backend as tb  # noqa: E402
from tehm.activation import instantiate as inst  # noqa: E402


def _store(outcomes, sources):
    """outcomes: {transition_id: outcome}; sources: {rule_id: [{transition_id: {hole: value}}]}"""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE tehm_transitions (transition_id TEXT, outcome TEXT)")
    c.execute("CREATE TABLE tehm_rule_sources (rule_id TEXT, episode_id TEXT, source_substitution_json TEXT)")
    c.execute("CREATE TABLE tehm_episode_steps (episode_id TEXT, transition_id TEXT)")
    for t, o in outcomes.items():
        c.execute("INSERT INTO tehm_transitions VALUES (?,?)", (t, o))
    for rule, subs_list in sources.items():
        for i, subs in enumerate(subs_list):
            ep = f"{rule}_ep{i}"
            c.execute("INSERT INTO tehm_rule_sources VALUES (?,?,?)", (rule, ep, json.dumps(subs)))
            for t in subs:
                c.execute("INSERT INTO tehm_episode_steps VALUES (?,?)", (ep, t))
    return c


def test_f1_net_zero_evidence_is_not_trialled_but_promoted_rules_are():
    c = _store({"t1": "PASS", "t2": "REGRESSION", "t3": "NEUTRAL"},
               {"r": [{"t1": {}}, {"t2": {}}, {"t3": {}}]})
    assert not tb._trial_admissible(c, "r", "candidate")       # 1 positive vs 1 harmful
    assert tb._trial_admissible(c, "r", "promoted")
    c2 = _store({"t1": "PASS", "t2": "PASS"}, {"r": [{"t1": {}}, {"t2": {}}]})
    assert tb._trial_admissible(c2, "r", "candidate")


def test_f1_counts_only_the_rules_own_applications():
    # each source episode is [the failure the rule repaired, the rule's PASS]: not net-zero evidence
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE tehm_transitions (transition_id TEXT, outcome TEXT)")
    c.execute("CREATE TABLE tehm_rule_sources (rule_id TEXT, episode_id TEXT, source_substitution_json TEXT)")
    c.execute("CREATE TABLE tehm_episode_steps (episode_id TEXT, transition_id TEXT)")
    for ep, fail, ok in (("e1", "f1", "p1"), ("e2", "f2", "p2")):
        c.executemany("INSERT INTO tehm_transitions VALUES (?,?)", [(fail, "FAIL"), (ok, "PASS")])
        c.executemany("INSERT INTO tehm_episode_steps VALUES (?,?)", [(ep, fail), (ep, ok)])
        c.execute("INSERT INTO tehm_rule_sources VALUES ('r',?,?)", (ep, json.dumps({ok: {"$H0": "13-30"}})))
    assert tb._source_outcome_profile(c, "r")["harmful"] == 2               # whole episodes
    assert tb._source_outcome_profile(c, "r", own_only=True)["harmful"] == 0
    assert tb._trial_admissible(c, "r", "candidate")


def test_f2_only_verified_witnesses_of_the_same_situation_vote():
    c = _store({"a": "PASS", "b": "PASS", "x": "REGRESSION"},
               {"r": [{"a": {"$H0": "le12", "$H1": "li.3", "$H3": 22}},
                      {"b": {"$H0": "13-30", "$H1": "m3.2", "$H3": 8}},
                      {"x": {"$H0": "le12", "$H1": "li.3", "$H3": 40}}]})
    assert sorted(tb._hole_witnesses(c, "r")["$H3"]) == [8, 22]                    # REGRESSION never votes
    assert tb._hole_witnesses(c, "r", bound={"$H0": "le12", "$H1": "li.3"})["$H3"] == [22]
    assert tb._hole_witnesses(c, "r", bound={"$H0": "13-30", "$H1": "li.3"}) == {}  # no matching regime
    rule = {"before_pattern": {"situation.util_band": "$H0", "situation.violation_class": "$H1",
                               "situation.platform": "sky130hs"}}
    assert tb._situation_holes(rule, {"util_band": "le12", "violation_class": "li.3"}) == {
        "$H0": "le12", "$H1": "li.3"}


def test_f3_mixed_sign_deltas_leave_the_knob_unresolved():
    assert inst._coherent_median([8, 16, 22]) == 16
    assert inst._coherent_median([-8, -6]) == -7
    assert inst._coherent_median([-8, 8, 22]) is None
    after = {"rewrite.knob.CORE_UTILIZATION.delta": "$H3"}
    edits, unresolved, _ = inst._multi_knob_edits(["CORE_UTILIZATION"], after, {}, {"CORE_UTILIZATION": "11"},
                                                  {"$H3": [-8, 22]})
    assert edits == {} and unresolved == ["CORE_UTILIZATION"]
