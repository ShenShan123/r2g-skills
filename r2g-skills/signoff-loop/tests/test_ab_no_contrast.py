"""An A/B trial is a comparison; identical arm configs mean there was none.

Every strategy in the catalog applies through `config_edits` -- including
antenna_diode_repair, whose edits are {SKIP_ANTENNA_REPAIR: 1,
MAX_REPAIR_ANTENNAS_ITER_DRT: 10} -- so the arm's config.mk knob set is a
complete fingerprint of what it applied, and no layout digest is needed.

The shape this catches: ab_runner.plan_trial's Tier 2 (`fixhist_platform`)
resolves subjects out of fix history because "a recipe that SUCCEEDS clears the
symptom, so Tier 1 is empty for exactly the recipes worth promoting". A
fix-history subject is already repaired, so arm B's fresh check reads clean, the
fixer applies nothing, and both arms re-grade the same layout.
antenna_diode_repair sits at 94 trials / 0 promotions that way, 88 of them
`fixhist_platform`, with both arms' judgeable metrics identical in all 94.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "loop"))
import engineer_loop as el  # noqa: E402

BASE = """export DESIGN_NAME = aes
export PLATFORM = nangate45
export CORE_UTILIZATION = 40
"""
BLOCK = """# >>> r2g signoff-fix (auto) >>>
export SKIP_ANTENNA_REPAIR = 1
export MAX_REPAIR_ANTENNAS_ITER_DRT = 10
# <<< r2g signoff-fix (auto) <<<
"""


def _arm(tmp_path, name, text):
    d = tmp_path / name / "constraints"
    d.mkdir(parents=True)
    (d / "config.mk").write_text(text)
    return str(tmp_path / name)


SAME = [{"is_success": True, "fix_iters": None, "outcome_score": 0.8833,
         "judged_on": "signoff"}]


def test_identical_arms_are_vetoed(tmp_path):
    """Both arms reset to baseline and neither applied anything -- the antenna shape:
    94 trials where the configs AND both arms' metrics matched."""
    a = _arm(tmp_path, "armA", BASE)
    b = _arm(tmp_path, "armB", BASE)
    v = el._ab_no_contrast(a, b, SAME, SAME)
    assert v and v.startswith("no_contrast:knobs_and_results_identical")


def test_identical_knobs_but_diverging_results_is_not_vetoed(tmp_path):
    """Contrast can live outside config.mk. A synth backend-abort pair is the
    counter-example: arm A memcap-aborts and arm B clears it, so the arms differ
    even where the recorded configs do not (the recovery knob is written by
    _apply_recipe_strategy at arm execution, which a flows-free replay may not
    show). Pinned because vetoing on config alone broke exactly this case."""
    a = _arm(tmp_path, "armA", BASE)
    b = _arm(tmp_path, "armB", BASE)
    a_s = [{"is_success": False, "judged_on": "synth:not_cleared"}]
    b_s = [{"is_success": True, "judged_on": "synth:cleared"}]
    assert el._ab_no_contrast(a, b, a_s, b_s) is None


def test_arm_b_applying_the_recipe_is_not_vetoed(tmp_path):
    a = _arm(tmp_path, "armA", BASE)
    b = _arm(tmp_path, "armB", BASE + BLOCK)
    assert el._ab_no_contrast(a, b, SAME, SAME) is None


def test_both_arms_applying_the_same_recipe_is_vetoed(tmp_path):
    """Equal is equal: a treated control matched by arm B is still no comparison."""
    a = _arm(tmp_path, "armA", BASE + BLOCK)
    b = _arm(tmp_path, "armB", BASE + BLOCK)
    assert el._ab_no_contrast(a, b, SAME, SAME) is not None


def test_bare_export_strategies_are_covered(tmp_path):
    """pin_perimeter_floor and the place/synth relief write BARE exports outside
    the auto-block, which is why the WHOLE knob set is digested, not just the
    block. Pinned so a later 'only digest the block' simplification cannot
    silently stop covering them."""
    a = _arm(tmp_path, "armA", BASE)
    b = _arm(tmp_path, "armB", BASE + "export PIN_PERIMETER_FLOOR = 110\n")
    assert el._ab_no_contrast(a, b, SAME, SAME) is None


def test_formatting_and_order_cannot_manufacture_contrast(tmp_path):
    a = _arm(tmp_path, "armA", BASE + BLOCK)
    shuffled = ("# a comment\n\nexport PLATFORM=nangate45\n"
                "export CORE_UTILIZATION   =    40\n"
                "export MAX_REPAIR_ANTENNAS_ITER_DRT = 10\n"
                "export DESIGN_NAME = aes\n"
                "export SKIP_ANTENNA_REPAIR = 1\n")
    b = _arm(tmp_path, "armB", shuffled)
    assert el._ab_no_contrast(a, b, SAME, SAME) is not None


def test_missing_config_is_unknown_not_identical(tmp_path):
    """Unknown must never be read as evidence of sameness."""
    a = _arm(tmp_path, "armA", BASE)
    (tmp_path / "armB" / "constraints").mkdir(parents=True)
    assert el._arm_knob_digest(str(tmp_path / "armB")) is None
    assert el._ab_no_contrast(a, str(tmp_path / "armB"), SAME, SAME) is None


def test_digest_counts_knobs(tmp_path):
    a = _arm(tmp_path, "armA", BASE)
    digest, n = el._arm_knob_digest(a)
    assert n == 3 and len(digest) == 12


def test_arm_local_paths_do_not_manufacture_contrast(tmp_path):
    """The arm dir name is unique per arm AND per repeat -- _localize_arm_sdc
    repoints SDC_FILE at the arm's own constraint.sdc, and VERILOG_FILES follows
    the copytree. Hashing those raw made every digest unique and this veto dead
    code: on eight real arm dirs the two repeats of the SAME control arm, both
    with an empty fix block, hashed differently (f9e6972d2df5 vs d0c5670df51d).
    Pinned so the normalization cannot be dropped as redundant."""
    a = _arm(tmp_path, "d_abA_r0", BASE)
    b = _arm(tmp_path, "d_abA_r1", BASE)
    da, db = el._arm_knob_digest(a), el._arm_knob_digest(b)
    assert da[0] == db[0], "two repeats of one arm must hash the same"
    v = el._ab_no_contrast(a, b, SAME, SAME)
    assert v and v.startswith("no_contrast")


def test_normalization_does_not_hide_a_real_recipe(tmp_path):
    """Same check in the other direction: the paths are normalized, the RECIPE
    is not. Arm B carrying the antenna knobs must still read as contrast."""
    a = _arm(tmp_path, "d_abA_r0", BASE)
    b = _arm(tmp_path, "d_abB_r0", BASE + BLOCK)
    assert el._arm_knob_digest(a)[0] != el._arm_knob_digest(b)[0]
    assert el._ab_no_contrast(a, b, SAME, SAME) is None


# ── the layout half of the fingerprint ──────────────────────────────────────
def _with_def(tmp_path, name, cfg_text, def_text):
    p = _arm(tmp_path, name, cfg_text)
    d = os.path.join(p, "backend", "RUN_2026-10-14_00-00-00_1_aaaa", "results")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "6_final.def"), "w").write(def_text)
    return p


DEF_A = "VERSION 5.8 ;\nDESIGN top ;\nCOMPONENTS 100 ;\nEND COMPONENTS\nEND DESIGN\n"
DEF_B = "VERSION 5.8 ;\nDESIGN top ;\nCOMPONENTS 101 ;\nEND COMPONENTS\nEND DESIGN\n"


def test_layout_digest_reads_components(tmp_path):
    p = _with_def(tmp_path, "d_abA_r0", BASE, DEF_A)
    dg, comp = el._arm_layout_digest(p)
    assert comp == 100 and len(dg) == 12


def test_identical_knobs_but_differing_layout_is_not_vetoed(tmp_path):
    """A strategy acting OUTSIDE config.mk -- a targeted `repair_antennas -net`,
    a hook-driven insertion -- leaves both knob sets identical. Without the
    layout half this vetoes a trial that genuinely tested something, which is
    exactly the antenna gap's shape: on one apb_gpio layout
    `check_antennas -verbose` reports 0 net and 0 pin violations while the deck
    reports 16, so no config value can reach those nets."""
    a = _with_def(tmp_path, "d_abA_r0", BASE, DEF_A)
    b = _with_def(tmp_path, "d_abB_r0", BASE, DEF_B)
    assert el._arm_knob_digest(a)[0] == el._arm_knob_digest(b)[0]
    assert el._arm_layout_digest(a)[0] != el._arm_layout_digest(b)[0]
    assert el._ab_no_contrast(a, b, SAME, SAME) is None


def test_identical_knobs_and_identical_layout_is_still_vetoed(tmp_path):
    """The flow is deterministic -- one config, three runs, two NUM_CORES values,
    byte-identical DEF md5 fc4bd8bf9769 / COMPONENTS 1398 -- so an identical DEF
    means the arms really did the same physical work."""
    a = _with_def(tmp_path, "d_abA_r0", BASE, DEF_A)
    b = _with_def(tmp_path, "d_abB_r0", BASE, DEF_A)
    v = el._ab_no_contrast(a, b, SAME, SAME)
    assert v and v.startswith("no_contrast")


def test_missing_def_is_unknown_not_identical(tmp_path):
    """An arm that never produced a layout is unknown. The veto must then rest on
    the result check alone rather than reading absence as sameness."""
    a = _with_def(tmp_path, "d_abA_r0", BASE, DEF_A)
    b = _arm(tmp_path, "d_abB_r0", BASE)          # no DEF at all
    assert el._arm_layout_digest(b) is None
    a_s = [{"is_success": False, "judged_on": "x"}]
    b_s = [{"is_success": True, "judged_on": "y"}]
    assert el._ab_no_contrast(a, b, a_s, b_s) is None
