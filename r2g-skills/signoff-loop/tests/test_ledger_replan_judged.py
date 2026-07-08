"""A re-planned A/B arm must be RE-judged (2026-06-27 audit). When plan_arms re-plans a
candidate whose arm dir survived a prior wave, Ledger.add resets the entry to 'pending' so
it re-runs -- but it used to KEEP the prior wave's `judged=True`. judge_finished_trials
filters `not judged`, so the re-run's verdict was never recorded: a surviving-dir A/B
candidate re-ran every wave but could never promote (and starved _ab_coverage_gap of trials).
This was why the large-pin place class never promoted via the perimeter die. The Ledger now
drops a stale `judged` on any 'pending' event, in BOTH the in-memory add and the JSONL reload.
"""
import engineer_loop as el


def _prior_wave(led):
    """Simulate a prior wave: an arm ran, escalated, and was JUDGED."""
    led.add({"design": "x_abB", "project_path": "/p/x_abB", "kind": "ab_arm",
             "arm": "B", "strategy": "core_util_relief", "ab_key": {}})
    led.set_state("x_abB", "escalated", reason="place_arm_failed")
    led.set_state("x_abB", "escalated", judged=True)


def test_replan_clears_stale_judged_in_memory(tmp_path):
    led = el.Ledger(tmp_path / "l.jsonl")
    _prior_wave(led)
    assert led._entries["x_abB"]["judged"] is True            # prior wave judged it
    # A later wave RE-PLANS the same arm (no 'state'/'judged' in the re-plan entry).
    led.add({"design": "x_abB", "project_path": "/p/x_abB", "kind": "ab_arm",
             "arm": "B", "strategy": "core_util_relief", "ab_key": {}})
    e = led._entries["x_abB"]
    assert e["state"] == "pending"                            # will re-run
    assert not e.get("judged")                                # ...and will be RE-judged


def test_replan_clears_stale_judged_after_reload(tmp_path):
    """Each wave is a fresh process that RELOADS the ledger from JSONL -- the reload must
    apply the same invariant, else the bug survives the restart."""
    p = tmp_path / "l.jsonl"
    led = el.Ledger(p)
    _prior_wave(led)
    led.add({"design": "x_abB", "project_path": "/p/x_abB", "kind": "ab_arm",
             "arm": "B", "strategy": "core_util_relief", "ab_key": {}})   # re-plan -> pending
    # Reload from disk in a fresh Ledger (new wave process).
    led2 = el.Ledger(p)
    e = led2._entries["x_abB"]
    assert e["state"] == "pending"
    assert not e.get("judged"), "reload kept a stale judged -> re-run never re-judged"


def test_terminal_event_keeps_judged(tmp_path):
    """The invariant is scoped to 'pending' events only: marking judged on a terminal arm
    (the normal judge path) must still stick."""
    led = el.Ledger(tmp_path / "l.jsonl")
    _prior_wave(led)
    assert led._entries["x_abB"].get("judged") is True

    # After re-plan + a real re-run reaching terminal, the judge can mark it again.
    led.add({"design": "x_abB", "project_path": "/p/x_abB", "kind": "ab_arm",
             "arm": "B", "strategy": "core_util_relief", "ab_key": {}})
    assert not led._entries["x_abB"].get("judged")            # re-plan cleared it
    led.set_state("x_abB", "clean")
    led.set_state("x_abB", "clean", judged=True)              # judge marks the re-run
    assert led._entries["x_abB"].get("judged") is True
