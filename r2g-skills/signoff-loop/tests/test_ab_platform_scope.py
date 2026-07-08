"""ab-drain must be scoped to THIS round's platform (2026-07-01 FINDING #3).

`plan_arms_for_candidates` used to plan A/B arms for EVERY pending candidate in the shared
`recipe_status`, regardless of platform. On a sky130 round it therefore planned asap7 arms
(slow asap7.lydrc DRC that can NEVER promote -- asap7 is not DRC-clean-able) and nangate45
arms, wedging the wave for HOURS (wave 1 stuck 6h+). `_ledger_round_platform` derives the
round's platform from the ledger's base entries so the drain skips off-platform candidates,
leaving them 'candidate' for validation in their OWN platform's round. Indeterminate ->
None -> scope disabled (fail-open, never wrongly starve a legitimate candidate).
"""
import engineer_loop as el


def _add(led, design, platform, kind="normal"):
    led.add({"design": design, "project_path": f"/p/{design}",
             "platform": platform, "kind": kind})


def test_round_platform_all_same(tmp_path):
    led = el.Ledger(tmp_path / "l.jsonl")
    for i in range(5):
        _add(led, f"d{i}", "sky130hd")
    assert el._ledger_round_platform(led) == "sky130hd"


def test_round_platform_ignores_arm_entries(tmp_path):
    # arm entries may carry OTHER platforms (cross-platform arms already materialized);
    # they must NOT sway the round-platform derivation.
    led = el.Ledger(tmp_path / "l.jsonl")
    for i in range(5):
        _add(led, f"d{i}", "sky130hd")
    _add(led, "x_abB_antenna__0", "nangate45", kind="ab_arm")
    _add(led, "y_abA_density__0", "asap7", kind="ab_arm")
    assert el._ledger_round_platform(led) == "sky130hd"


def test_round_platform_clear_majority(tmp_path):
    # a stray off-platform base entry does not flip a clear-majority round.
    led = el.Ledger(tmp_path / "l.jsonl")
    for i in range(4):
        _add(led, f"s{i}", "sky130hd")
    _add(led, "stray", "asap7")
    assert el._ledger_round_platform(led) == "sky130hd"


def test_round_platform_indeterminate_is_none(tmp_path):
    # a genuinely mixed base ledger (no clear majority) -> None -> scope disabled (fail-open).
    led = el.Ledger(tmp_path / "l.jsonl")
    _add(led, "a", "sky130hd")
    _add(led, "b", "asap7")
    assert el._ledger_round_platform(led) is None


def test_round_platform_empty_is_none(tmp_path):
    led = el.Ledger(tmp_path / "l.jsonl")
    assert el._ledger_round_platform(led) is None
