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
    # P0-2 (failure-patterns #48): the LEARNED indexed-recipe path FAILS CLOSED. A
    # candidate (not yet promoted) is stripped, AND an absent-row strategy is NO LONGER
    # grandfathered-live here — a learned recipe with no lifecycle row means its enqueue
    # never landed, so it must not be trusted. (get_status's cold-start default stays
    # 'promoted' for the STATIC catalog path — see the test below.)
    assert "antenna_diode_repair" not in out["strategies"]
    assert "other_strat" not in out["strategies"]


def test_filter_promoted_keeps_promoted_strategy(tmp_path):
    """The positive control: a genuinely promoted strategy survives filter_promoted."""
    conn = _conn(tmp_path)
    recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    recipe_lifecycle.promote(conn, **KEY, evidence="ab_trial:1")
    entry = {"strategies": {KEY["strategy"]: {"attempts": 1, "successes": 1},
                            "other_strat": {"attempts": 2, "successes": 0}}}
    out = recipe_lifecycle.filter_promoted(conn, entry, symptom_id=KEY["symptom_id"],
                                           design_class=KEY["design_class"],
                                           platform=KEY["platform"])
    assert list(out["strategies"]) == ["antenna_diode_repair"]


def test_get_status_default_param_separates_coldstart_from_learned(tmp_path):
    """get_status keeps GRANDFATHERED as its DEFAULT default (the STATIC cold-start path
    at diagnose_signoff_fix._annotate_live_gates keeps working), but a caller can pass
    default=UNROSTERED to fail closed (the LEARNED filter_promoted path)."""
    conn = _conn(tmp_path)
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"          # cold-start
    assert recipe_lifecycle.get_status(
        conn, **KEY, default=recipe_lifecycle.UNROSTERED) == "unrostered"  # fail-closed


def test_ensure_rostered_covers_enqueue_crash_gap(tmp_path):
    """P0-2 self-heal: heuristics carry a recipe whose recipe_status enqueue never
    landed (simulating a crashed diff_and_enqueue). unrostered_keys flags it; then
    ensure_rostered rosters it as a CANDIDATE (fail-closed, never fabricated promoted),
    so filter_promoted stops silently dropping it and the A/B loop can validate it."""
    conn = _conn(tmp_path)
    heur = _heur(2, 1)
    # no diff_and_enqueue was ever run -> the concrete key has no row
    assert recipe_lifecycle.unrostered_keys(conn, heur) == [tuple(KEY.values())]
    rostered = recipe_lifecycle.ensure_rostered(conn, heur)
    assert rostered == [tuple(KEY.values())]
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"   # NOT promoted
    assert recipe_lifecycle.unrostered_keys(conn, heur) == []        # gap closed
    # idempotent: a second call rosters nothing and never clobbers the existing row
    assert recipe_lifecycle.ensure_rostered(conn, heur) == []


def test_ensure_rostered_skips_nondivergent(tmp_path):
    """ensure_rostered must NOT enqueue a NONDIVERGENT strategy (park_nondivergent owns
    those) — otherwise it would re-create the eternal-candidate rows the enqueue filter
    exists to prevent."""
    conn = _conn(tmp_path)
    strat = sorted(recipe_lifecycle.NONDIVERGENT_STRATEGIES)[0]
    heur = {"generation": 2, "recipes": {"sig1": {"crypto/small": {"nangate45": {
        "strategies": {strat: {"attempts": 1, "successes": 1, "failures": 0,
                               "wins": 0}}, "n_sessions": 1}}}}}
    assert recipe_lifecycle.unrostered_keys(conn, heur) == []
    assert recipe_lifecycle.ensure_rostered(conn, heur) == []
