"""Incremental A/B judging (2026-06-27 latency fix): the drain now calls
judge_finished_trials after EACH arm completes (not once at end-of-drain) so a finished
place win surfaces + promotes mid-drain instead of waiting on the slowest unrelated arm
(wave 11 hid its promotions behind a ~12h drain). The safety property that makes the
per-completion calls correct: judge_finished_trials judges a pair ONLY when BOTH its arms
are terminal, skips a pair with a still-running arm, and NEVER re-judges (idempotent).
"""
import engineer_loop as el


def _metric(conn, pp, timing=False, synth=False, target=None):
    return {"is_success": pp.endswith("abB"), "wall_s": 10.0,
            "fix_iters": None,
            "outcome_score": 0.5 if pp.endswith("abB") else 0.333}


def _arm(design, arm, state, key):
    return {"design": design, "project_path": f"/p/{design}", "kind": "ab_arm",
            "arm": arm, "strategy": "core_util_relief", "repeat": 0, "state": state,
            "ab_key": key, "match_level": "exact"}


def test_judges_only_both_terminal_pairs_and_is_idempotent(tmp_path, monkeypatch):
    import ab_runner
    led = el.Ledger(tmp_path / "l.jsonl")
    key = {"symptom_id": "s", "design_class": "logic/small",
           "platform": "nangate45", "strategy": "core_util_relief"}
    # P1: both arms terminal (A escalated/fail, B clean/success).
    led.add(_arm("d1_abA", "A", "escalated", key))
    led.add(_arm("d1_abB", "B", "clean", key))
    # P2: arm A terminal, arm B STILL RUNNING (state 'flow') -> must NOT be judged yet.
    led.add(_arm("d2_abA", "A", "escalated", key))
    led.add(_arm("d2_abB", "B", "flow", key))

    # arm metric: any *_abB succeeds, any *_abA fails (a decisive divergence -> win).
    # (target= is the judge-v2 symptom-target kwarg, 2026-07-04.)
    monkeypatch.setattr(el, "_arm_metric",
                        lambda conn, pp, timing=False, synth=False, target=None: {
        "is_success": pp.endswith("abB"), "wall_s": 10.0, "fix_iters": None,
        "outcome_score": 0.5 if pp.endswith("abB") else 0.333})
    recorded = []
    monkeypatch.setattr(ab_runner, "record_trial",
                        lambda conn, **kw: recorded.append(kw))

    # 1st incremental judge: only P1 is complete.
    el.judge_finished_trials(led, None)
    assert len(recorded) == 1                          # P1 judged
    assert recorded[0]["verdict"] == "win"             # B succeeds where A fails
    assert led._entries["d1_abA"].get("judged") and led._entries["d1_abB"].get("judged")
    assert not led._entries["d2_abA"].get("judged")    # P2 incomplete -> skipped

    # Idempotent: re-judging with nothing newly terminal records NO new trial.
    el.judge_finished_trials(led, None)
    assert len(recorded) == 1

    # P2's arm B finishes -> the next judge records P2 (and only P2).
    led.set_state("d2_abB", "clean")
    el.judge_finished_trials(led, None)
    assert len(recorded) == 2
    assert led._entries["d2_abA"].get("judged") and led._entries["d2_abB"].get("judged")


def test_same_ab_corpus_promotion_does_not_cancel_remaining_subject(
        tmp_path, monkeypatch):
    """The first incremental win must not erase the second planned subject."""
    import ab_runner
    import knowledge_db
    conn = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(conn)
    key = {"symptom_id": "s", "design_class": "logic/small",
           "platform": "sky130hd", "strategy": "core_util_relief"}
    conn.execute(
        "INSERT INTO recipe_status (symptom_id,design_class,platform,strategy,"
        "status,provenance,status_version,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (*key.values(), "promoted", "ab_corpus:1w0l", 2,
         "2026-08-13T00:00:00-07:00"))
    conn.commit()
    led = el.Ledger(tmp_path / "l.jsonl")
    for role, state in (("A", "escalated"), ("B", "clean")):
        entry = _arm(f"d2_ab{role}", role, state, key)
        entry["recipe_status_version"] = 1
        led.add(entry)
    monkeypatch.setattr(el, "_arm_metric", _metric)
    monkeypatch.setattr(el, "_arm_spec_mismatch", lambda *a, **k: None)
    monkeypatch.setattr(el, "_arm_baseline_divergence", lambda *a, **k: None)
    monkeypatch.setattr(el, "_ab_new_drc_regression", lambda *a, **k: None)
    monkeypatch.setattr(el, "_ab_global_regression", lambda *a, **k: None)
    recorded = []
    monkeypatch.setattr(ab_runner, "record_trial",
                        lambda conn, **kw: recorded.append(kw) or 2)

    el.judge_finished_trials(led, conn)

    assert len(recorded) == 1
    assert recorded[0]["verdict"] == "win"


def test_external_lifecycle_move_still_cancels_subject(tmp_path, monkeypatch):
    """An operator/live-regression withdrawal remains authoritative."""
    import ab_runner
    import knowledge_db
    conn = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(conn)
    key = {"symptom_id": "s", "design_class": "logic/small",
           "platform": "sky130hd", "strategy": "core_util_relief"}
    conn.execute(
        "INSERT INTO recipe_status (symptom_id,design_class,platform,strategy,"
        "status,provenance,status_version,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (*key.values(), "shadow", "repeated_regression", 2,
         "2026-08-13T00:00:00-07:00"))
    conn.commit()
    led = el.Ledger(tmp_path / "l.jsonl")
    for role, state in (("A", "escalated"), ("B", "clean")):
        entry = _arm(f"d2_ab{role}", role, state, key)
        entry["recipe_status_version"] = 1
        led.add(entry)
    monkeypatch.setattr(el, "_arm_metric", _metric)
    recorded = []
    monkeypatch.setattr(ab_runner, "record_trial",
                        lambda conn, **kw: recorded.append(kw) or 2)

    el.judge_finished_trials(led, conn)

    assert recorded == []
    assert led.get("d2_abA").get("judged")
    assert led.get("d2_abB").get("judged")


def test_waits_for_full_repeat_cohort_before_judging(tmp_path, monkeypatch):
    """2026-07-04: incremental judging FRAGMENTED k-repeat trials — it judged
    whatever repeat subset was terminal at each pass, so a k=2 trial became a
    2-vs-1 (cost tiebreak disabled -> success_tie_insufficient_repeats) or two
    1-vs-1 fragments, and a straggler repeat could strand unjudged forever
    (its siblings were judged=True, leaving it a one-sided pair). A pair is
    judged only when its WHOLE repeat cohort is terminal; zombie non-terminal
    entries already marked judged (historical fragments) do not block."""
    import ab_runner
    import engineer_loop as el
    led = el.Ledger(tmp_path / "l.jsonl")
    key = {"symptom_id": "s", "design_class": "c/small",
           "platform": "sky130hd", "strategy": "pdn_die_floor"}

    def _arm(name, arm, state, repeat):
        led.add({"design": name, "project_path": f"/p/{name}",
                 "platform": "sky130hd", "kind": "ab_arm", "arm": arm,
                 "strategy": "pdn_die_floor", "repeat": repeat, "check": "both",
                 "ab_key": key, "match_level": "exact"})
        led.set_state(name, state)

    _arm("d_abA_pdn_0", "A", "clean", 0)
    _arm("d_abA_pdn_1", "A", "clean", 1)
    _arm("d_abB_pdn_0", "B", "clean", 0)
    _arm("d_abB_pdn_1", "B", "flow", 1)          # repeat still running

    monkeypatch.setattr(el, "_arm_metric",
                        lambda conn, pp, timing=False, synth=False, target=None: {
                            "is_success": True, "wall_s": 10.0, "fix_iters": None,
                            "outcome_score": 0.5})
    recorded = []
    monkeypatch.setattr(ab_runner, "record_trial",
                        lambda conn, **kw: recorded.append(kw) or 1)

    el.judge_finished_trials(led, None)
    assert recorded == []                        # cohort incomplete: no fragment verdict
    assert not led.get("d_abA_pdn_0").get("judged")

    led.set_state("d_abB_pdn_1", "clean")        # last repeat lands
    el.judge_finished_trials(led, None)
    assert len(recorded) == 1                    # ONE trial, full cohort
    assert recorded[0]["metrics"]["repeats"] == {"A": 2, "B": 2}


def test_zombie_judged_nonterminal_entry_does_not_block_cohort(tmp_path, monkeypatch):
    """A historical fragment can leave a non-terminal-state entry already marked
    judged (state carried by set_state). It must not deadlock future judging."""
    import ab_runner
    import engineer_loop as el
    led = el.Ledger(tmp_path / "l.jsonl")
    key = {"symptom_id": "s", "design_class": "c/small",
           "platform": "sky130hd", "strategy": "pdn_die_floor"}
    led.add({"design": "d_abA_pdn_0", "project_path": "/p/a0",
             "platform": "sky130hd", "kind": "ab_arm", "arm": "A",
             "strategy": "pdn_die_floor", "repeat": 0, "check": "both",
             "ab_key": key})
    led.set_state("d_abA_pdn_0", "clean")
    led.add({"design": "d_abB_pdn_0", "project_path": "/p/b0",
             "platform": "sky130hd", "kind": "ab_arm", "arm": "B",
             "strategy": "pdn_die_floor", "repeat": 0, "check": "both",
             "ab_key": key})
    led.set_state("d_abB_pdn_0", "clean")
    # zombie: judged fragment stuck in a non-terminal state
    led.add({"design": "d_abB_pdn_9", "project_path": "/p/b9",
             "platform": "sky130hd", "kind": "ab_arm", "arm": "B",
             "strategy": "pdn_die_floor", "repeat": 9, "check": "both",
             "ab_key": key})
    led.set_state("d_abB_pdn_9", "flow", judged=True)

    monkeypatch.setattr(el, "_arm_metric",
                        lambda conn, pp, timing=False, synth=False, target=None: {
                            "is_success": True, "wall_s": 10.0, "fix_iters": None,
                            "outcome_score": 0.5})
    recorded = []
    monkeypatch.setattr(ab_runner, "record_trial",
                        lambda conn, **kw: recorded.append(kw) or 1)
    el.judge_finished_trials(led, None)
    assert len(recorded) == 1                    # zombie ignored, pair judged
