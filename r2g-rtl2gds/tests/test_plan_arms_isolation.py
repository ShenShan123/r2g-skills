"""plan_arms_for_candidates must ISOLATE a candidate whose plan_trial raises.

Root cause (2026-06-28): `plan_trial` reads state that can race the campaign's concurrent
heuristics.json / ingest writes and raise transiently (observed: an intermittent
`KeyError 'design'`). `plan_arms_for_candidates` called it with NO try/except, so ONE
crashing candidate aborted the entire planning loop and stranded every candidate AFTER it.
`synth_memory_relax` (the LAST of 33 pending candidates) sat at 0 A/B trials for hours —
any transient crash earlier in the list blocked it on every drain, so it could never
promote. Fix: skip + log a crashing candidate (stays 'candidate', re-plans next drain),
never let it abort the loop.
"""
import engineer_loop as el


def test_plan_arms_isolates_a_crashing_candidate(tmp_path, monkeypatch):
    import ab_runner
    import recipe_lifecycle

    # a real subject dir for the GOOD candidate's arm copytree
    subj = tmp_path / "good_subject"
    (subj / "constraints").mkdir(parents=True)
    (subj / "constraints" / "config.mk").write_text("export DESIGN_NAME = g\n")

    bad = {"symptom_id": "badsym", "design_class": "c/l", "platform": "nangate45",
           "strategy": "strat_bad"}
    good = {"symptom_id": "goodsym", "design_class": "c/l", "platform": "nangate45",
            "strategy": "strat_good"}
    # bad is FIRST so, without isolation, its crash would abort before good is reached
    monkeypatch.setattr(recipe_lifecycle, "pending_candidates", lambda conn: [bad, good])
    monkeypatch.setattr(el, "_ab_coverage_gap", lambda conn, key: False)
    monkeypatch.setattr(el, "_symptom_check", lambda conn, sid, strat=None: "both")

    def fake_plan(conn, **k):
        if k["strategy"] == "strat_bad":
            raise KeyError("design")          # the transient race crash
        return {"designs": [{"design_name": "g", "project_path": str(subj),
                             "cell_count": 1}], "match_level": "exact"}
    monkeypatch.setattr(ab_runner, "plan_trial", fake_plan)

    led = el.Ledger(tmp_path / "l.jsonl")
    appended = el.plan_arms_for_candidates(led, conn=None, repeats=1)

    # the GOOD candidate (AFTER the crasher) still got its arms planned
    goods = [e for e in led.entries() if e.get("strategy") == "strat_good"]
    assert goods, "a crashing candidate aborted the loop and stranded the good candidate"
    assert {e["arm"] for e in goods} == {"A", "B"}    # both arms planned
    assert appended >= 2
    # the bad candidate planned nothing (skipped, not crashed)
    assert not [e for e in led.entries() if e.get("strategy") == "strat_bad"]
