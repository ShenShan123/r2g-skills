"""Arm A is a control only while it applies nothing.

_run_fix launches arm A with `--max-iters 0`, so a sample reporting
fix_iters>=1 is positive evidence the control repaired something; a win or loss
measured against a treated control compares two treatments, not a recipe
against a baseline. Measured on the shipped store (2026-10-12): 9 of 444 trials
carry such a control, 8 of them antenna_diode_repair.

The veto is keyed on PRESENCE of iterations, never absence: a config-edit
strategy lands its change before the flow and legitimately reports none
(density_relief shows both shapes across its 122 trials), so "no iterations"
cannot stand in for "no intervention".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "loop"))
import engineer_loop as el  # noqa: E402


def test_clean_control_is_not_vetoed():
    assert el._ab_control_intervened([{"is_success": True, "fix_iters": 0}]) is None
    assert el._ab_control_intervened([{"is_success": True, "fix_iters": None}]) is None
    assert el._ab_control_intervened([{"is_success": True}]) is None


def test_treated_control_is_vetoed():
    assert el._ab_control_intervened(
        [{"is_success": True, "fix_iters": 1}]) == "control_intervened"


def test_any_repeat_contaminates_the_trial():
    """One treated repeat is enough: the arm's samples are pooled into one LCB."""
    samples = [{"is_success": True, "fix_iters": None},
               {"is_success": True, "fix_iters": 2}]
    assert el._ab_control_intervened(samples) == "control_intervened"


def test_empty_and_crashed_arms_are_not_contamination():
    """A crashed arm (None sample) is judged `arm_no_samples`, not contamination."""
    assert el._ab_control_intervened([]) is None
    assert el._ab_control_intervened(None) is None
    assert el._ab_control_intervened([None]) is None


def test_absent_iters_never_implies_no_intervention():
    """The asymmetry this veto rests on, pinned so it cannot be 'tidied' into
    a symmetric check: absence must stay non-vetoing even when the arm clearly
    did work (a config-edit strategy reports no fix-loop iteration)."""
    config_edit_arm = [{"is_success": True, "fix_iters": None, "outcome_score": 0.88}]
    assert el._ab_control_intervened(config_edit_arm) is None
