"""Tier 0: an A/B subject must CURRENTLY exhibit the symptom and still be flowable.

Tier 1 asks run_violations over all history and Tier 2 resolves out of fix
history, so both can only offer an already-repaired subject -- Tier 1 because
runs/run_violations are immutable history (a design's OLD violating run still
matches after a later run cleared it) and Tier 2 by construction ("a recipe that
SUCCEEDS clears the symptom, so Tier 1 is empty for exactly the recipes worth
promoting"). A repaired subject makes arm A a treated arm and arm B's forced
recipe a no-op, so both arms re-grade the same layout and the trial ties:
antenna_diode_repair holds 94 trials / 0 promotions that way, 88 of them
`fixhist_platform`, with both arms' metrics identical in all 94, while carrying
53 `resolved` fix trajectories.

`os.path.isdir` is not a flowability test either. A harvested corpus run's
config.mk names `/work/constraint.sdc`, the CONTAINER path of the flow that
produced it, so the dir exists but the arm would abort on its first stage -- the
2026-07-03 ghost-subject failure in different clothes.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "knowledge"))
import ab_runner  # noqa: E402

RTL = "design.v"


def _proj(tmp_path, name, *, sdc_ok=True, rtl_ok=True):
    p = tmp_path / name
    (p / "constraints").mkdir(parents=True)
    rtl = tmp_path / RTL
    if rtl_ok:
        rtl.write_text("module top; endmodule\n")
    sdc = p / "constraints" / "constraint.sdc"
    sdc.write_text("create_clock -period 2 [get_ports clk]\n")
    sdc_path = str(sdc) if sdc_ok else "/work/constraint.sdc"
    (p / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = nangate45\n"
        f"export VERILOG_FILES = {rtl}\nexport SDC_FILE = {sdc_path}\n")
    return str(p)


# ── _is_flowable ────────────────────────────────────────────────────────────
def test_flowable_project(tmp_path):
    ok = _proj(tmp_path, "ok")
    assert ab_runner._is_flowable(ok, strict=True) is True
    assert ab_runner._is_flowable(ok) is True


def test_container_sdc_path_is_not_flowable(tmp_path):
    """The exact shape every harvested corpus run has before the bridge fix."""
    assert ab_runner._is_flowable(
        _proj(tmp_path, "badsdc", sdc_ok=False), strict=True) is False


def test_missing_rtl_is_not_flowable(tmp_path):
    assert ab_runner._is_flowable(
        _proj(tmp_path, "nortl", rtl_ok=False), strict=True) is False


def test_missing_config_is_unknown_not_broken(tmp_path):
    """The two modes. Tier 0 selects FOR judgeability and demands positive proof,
    so no config.mk means no subject. Tier 1 only needs to exclude subjects it
    can PROVE are broken -- a project with no config.mk is unreadable, not stale,
    and rejecting it there would retire the pre-existing tiers by accident
    (six of their tests build bare mkdir fixtures)."""
    (tmp_path / "empty").mkdir()
    e = str(tmp_path / "empty")
    assert ab_runner._is_flowable(e, strict=True) is False
    assert ab_runner._is_flowable(e) is True
    assert ab_runner._is_flowable("", strict=True) is False


def test_broken_config_is_rejected_in_both_modes(tmp_path):
    """A STALE config.mk is proof of breakage, so it fails leniently too."""
    bad = _proj(tmp_path, "stale", sdc_ok=False)
    assert ab_runner._is_flowable(bad, strict=True) is False
    assert ab_runner._is_flowable(bad) is False


# ── Tier 0 selection ────────────────────────────────────────────────────────
def _db(tmp_path):
    c = sqlite3.connect(":memory:")
    c.executescript("""
      CREATE TABLE runs (run_id TEXT PRIMARY KEY, design_name TEXT,
        project_path TEXT, cell_count INT, platform TEXT, design_class TEXT,
        lvs_status TEXT, ingested_at TEXT);
      CREATE TABLE run_violations (run_id TEXT, symptom_id TEXT);
      CREATE TABLE symptoms (symptom_id TEXT, check_type TEXT, class TEXT);
    """)
    return c


def _add(c, run_id, name, path, *, sid=None, lvs="clean", at="2026-10-01",
         cells=100):
    c.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
              (run_id, name, path, cells, "nangate45", "misc/small", lvs, at))
    if sid:
        c.execute("INSERT INTO run_violations VALUES (?,?)", (run_id, sid))


def _plan(c, n=1):
    return ab_runner.plan_trial(c, symptom_id="S", design_class="misc/small",
                                platform="nangate45",
                                strategy="antenna_diode_repair", n_designs=n)


def test_current_exhibitor_is_selected(tmp_path):
    c = _db(tmp_path)
    _add(c, "r1", "live", _proj(tmp_path, "live"), sid="S")
    t = _plan(c)
    assert t and t["match_level"].startswith("current_exhibitor")
    assert t["designs"][0]["design_name"] == "live"


def test_repaired_subject_is_not_a_tier0_subject(tmp_path):
    """The whole point: the symptom is on an OLD run, the LATEST run is clean.

    Tier 1 still matches it -- runs/run_violations are immutable history and that
    reachability is deliberate -- so this asserts only that TIER 0 declines it.
    The resulting tie is declared void by engineer_loop._ab_no_contrast; the
    division of labour is "Tier 0 supplies a judgeable subject, the veto names an
    unjudgeable trial", not "the planner hides repaired subjects"."""
    c = _db(tmp_path)
    p = _proj(tmp_path, "fixed")
    _add(c, "old", "fixed", p, sid="S", at="2026-09-01")   # violated then
    _add(c, "new", "fixed", p, sid=None, at="2026-10-01")  # clean now
    t = _plan(c)
    assert t is None or not t["match_level"].startswith("current_exhibitor")


def test_unflowable_exhibitor_is_skipped_in_every_tier(tmp_path):
    """Unflowable is absolute: no tier may offer a subject that cannot flow,
    because it aborts on its first stage whoever picked it."""
    c = _db(tmp_path)
    _add(c, "r1", "ghost", _proj(tmp_path, "ghost", sdc_ok=False), sid="S")
    assert _plan(c) is None


def test_lvs_failed_exhibitor_is_not_a_tier0_subject(tmp_path):
    """An LVS with no verdict leaves is_success unable to speak for the DRC
    symptom under test, so the arm's result would not be about the recipe.
    Tier 1 has no signoff filter, so again this pins TIER 0's choice only."""
    c = _db(tmp_path)
    _add(c, "r1", "nolvs", _proj(tmp_path, "nolvs"), sid="S", lvs="incomplete")
    t = _plan(c)
    assert t is None or not t["match_level"].startswith("current_exhibitor")


def test_cheapest_subject_first(tmp_path):
    c = _db(tmp_path)
    _add(c, "r1", "big", _proj(tmp_path, "big"), sid="S", cells=9000)
    _add(c, "r2", "small", _proj(tmp_path, "small"), sid="S", cells=90)
    t = _plan(c, n=2)
    assert [d["design_name"] for d in t["designs"]] == ["small", "big"]


# ── independent-subject identity ────────────────────────────────────────────
def _subj_db(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE runs (run_id TEXT, project_path TEXT, "
              "design_family TEXT, platform TEXT)")
    c.executemany("INSERT INTO runs VALUES (?,?,?,?)", rows)
    return c


def test_family_equal_to_platform_is_not_a_family():
    """infer_family takes the project dir's leading token, so an ingest path that
    names projects `<platform>__...` collapses its whole corpus into one family
    per platform -- then every trial on that platform is the SAME subject, wins
    can never exceed 1, and nothing clears ab_min_independent_wins()=2. The
    campaign bridge did exactly that: two genuine wins on two unrelated repos
    both resolved to `family:nangate45` and netted 1w."""
    c = _subj_db([("rA", "/p/nangate45__C1__topA", "nangate45", "nangate45"),
                  ("rB", "/p/nangate45__C2__topB", "nangate45", "nangate45")])
    a = ab_runner._trial_subject(c, "rA", "rA", "t1")
    b = ab_runner._trial_subject(c, "rB", "rB", "t2")
    assert a != b, "two unrelated repos must not collapse to one subject"
    assert not a.startswith("family:")


def test_a_real_family_still_collapses_its_tops():
    """The P1-11 intent is preserved: two tops from ONE RTL repo stay one vote."""
    c = _subj_db([("rA", "/p/wb2axip__C1__axi2axilite", "wb2axip", "nangate45"),
                  ("rB", "/p/wb2axip__C2__axilxbar", "wb2axip", "nangate45")])
    assert (ab_runner._trial_subject(c, "rA", "rA", "t1")
            == ab_runner._trial_subject(c, "rB", "rB", "t2") == "family:wb2axip")
