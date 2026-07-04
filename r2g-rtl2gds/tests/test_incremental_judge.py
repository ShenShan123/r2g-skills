"""Incremental A/B judging (2026-06-27 latency fix): the drain now calls
judge_finished_trials after EACH arm completes (not once at end-of-drain) so a finished
place win surfaces + promotes mid-drain instead of waiting on the slowest unrelated arm
(wave 11 hid its promotions behind a ~12h drain). The safety property that makes the
per-completion calls correct: judge_finished_trials judges a pair ONLY when BOTH its arms
are terminal, skips a pair with a still-running arm, and NEVER re-judges (idempotent).
"""
import engineer_loop as el


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
