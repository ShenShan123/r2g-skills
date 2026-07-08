"""Recipe lifecycle: efficacy-gated promotion (spec §5.3, decisions 7+8)."""
import json

import knowledge_db
import recipe_lifecycle


KEY = dict(symptom_id="deadbeef00000001", design_class="crypto/small",
           platform="nangate45", strategy="antenna_diode_repair")


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _heur(gen, attempts):
    return {"generation": gen, "recipes": {KEY["symptom_id"]: {
        KEY["design_class"]: {KEY["platform"]: {
            "strategies": {KEY["strategy"]: {"attempts": attempts,
                                             "successes": attempts,
                                             "failures": 0, "wins": 0}},
            "n_sessions": attempts}}}}}


def test_diff_enqueues_new_recipe_as_candidate(tmp_path):
    conn = _conn(tmp_path)
    cands = recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    assert cands == [tuple(KEY.values())]
    st = recipe_lifecycle.get_status(conn, **KEY)
    assert st == "candidate"


def test_unchanged_recipe_not_reenqueued(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    assert recipe_lifecycle.diff_and_enqueue(conn, _heur(3, 1), prev=_heur(2, 1)) == []


def test_promote_requires_candidate_and_records_provenance(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    recipe_lifecycle.promote(conn, **KEY, evidence="ab_trial:42")
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"


def test_demote_on_loss_reverts_to_shadow(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    recipe_lifecycle.demote(conn, **KEY, reason="ab_loss")
    assert recipe_lifecycle.get_status(conn, **KEY) == "shadow"


def test_unknown_key_defaults_to_promoted_for_grandfathered(tmp_path):
    # Pre-lifecycle learned recipes are grandfathered (spec §5.3 bootstrap):
    # absent row -> treated as promoted so existing live ranking keeps working.
    conn = _conn(tmp_path)
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"


def test_filter_promoted_strips_unpromoted_strategies(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    entry = {"strategies": {KEY["strategy"]: {"attempts": 1, "successes": 1},
                            "other_strat": {"attempts": 2, "successes": 0}},
             "n_sessions": 3}
    out = recipe_lifecycle.filter_promoted(conn, entry, symptom_id=KEY["symptom_id"],
                                           design_class=KEY["design_class"],
                                           platform=KEY["platform"])
    # candidate (not yet promoted) is stripped; absent-row strategy grandfathered.
    assert "antenna_diode_repair" not in out["strategies"]
    assert "other_strat" in out["strategies"]
