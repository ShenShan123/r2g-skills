"""A/B re-plan must NOT reset a clean-but-UNJUDGED arm back to pending (2026-06-30 asap7
Step-4 closure bug). plan_arms_for_candidates was calling led.add() unconditionally for every
arm; led.add defaults state='pending', so each plan cycle (run's _run_parallel AND ab-drain,
per wave) reset arms that had already reached a terminal state but were still awaiting their
pair's verdict. judge_finished_trials only judges a pair when BOTH arms are terminal + unjudged
SIMULTANEOUSLY, so the reset meant a complete A+B pair was never both-terminal at one judge
moment -> ab_trials_asap7=0, infinite re-plan loop, no promotion (arms cycled
plan->clean->re-plan->clean). The guard _arm_awaiting_judge() leaves a terminal-unjudged arm
alone so the judge can record it; a JUDGED terminal arm is still re-planned (Pattern 15).
"""
import engineer_loop as el


def _add_arm(led, design="x_abB"):
    led.add({"design": design, "project_path": f"/p/{design}", "kind": "ab_arm",
             "arm": "B", "strategy": "density_relief", "ab_key": {}})


def test_ledger_get_returns_entry_or_none(tmp_path):
    led = el.Ledger(tmp_path / "l.jsonl")
    assert led.get("absent") is None
    _add_arm(led, "x_abB")
    assert led.get("x_abB")["state"] == "pending"


def test_awaiting_judge_true_for_terminal_unjudged(tmp_path):
    led = el.Ledger(tmp_path / "l.jsonl")
    _add_arm(led, "x_abB")
    led.set_state("x_abB", "clean")                  # ran, terminal, NOT judged
    assert el._arm_awaiting_judge(led, "x_abB") is True
    # escalated also counts as terminal-awaiting-judge
    _add_arm(led, "y_abA")
    led.set_state("y_abA", "escalated", reason="r")
    assert el._arm_awaiting_judge(led, "y_abA") is True


def test_awaiting_judge_false_for_judged_terminal(tmp_path):
    led = el.Ledger(tmp_path / "l.jsonl")
    _add_arm(led, "x_abB")
    led.set_state("x_abB", "clean")
    led.set_state("x_abB", "clean", judged=True)     # already judged -> re-plan is OK
    assert el._arm_awaiting_judge(led, "x_abB") is False


def test_awaiting_judge_false_for_pending_or_absent(tmp_path):
    led = el.Ledger(tmp_path / "l.jsonl")
    assert el._arm_awaiting_judge(led, "absent") is False
    _add_arm(led, "x_abB")                           # pending (not terminal)
    assert el._arm_awaiting_judge(led, "x_abB") is False


def test_replan_does_not_reset_terminal_unjudged_arm(tmp_path):
    """The end-to-end invariant the closure fix protects: once an arm is clean+unjudged, a
    re-plan that goes through the _arm_awaiting_judge guard must leave it terminal (so the
    judge records the trial), NOT reset it to pending. Simulates the guarded re-plan."""
    led = el.Ledger(tmp_path / "l.jsonl")
    _add_arm(led, "x_abB")
    led.set_state("x_abB", "clean")                  # ran clean, awaiting its A-arm pair
    # plan_arms_for_candidates re-encounters this arm; the guard must skip the led.add.
    if not el._arm_awaiting_judge(led, "x_abB"):
        _add_arm(led, "x_abB")                       # (would reset to pending — must NOT happen)
    assert led.get("x_abB")["state"] == "clean", "re-plan reset a terminal-unjudged arm"
