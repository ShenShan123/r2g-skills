"""plan_trial must reach SUCCESSFUL recipes via the fix-history evidence tier.

Regression for the 2026-06-22 loop-closure bug. plan_trial's Tier 1 reads
``run_violations`` — the POST-fix residual snapshot. A recipe that *succeeds*
(``antenna_diode_repair`` clearing DRC to 0) leaves no residual row there, so for
exactly the recipes worth promoting Tier 1 is empty. The only fallback was a
heuristics name-list keyed on the bare DESIGN_NAME (``can_tx``), which never
matches the campaign's repo-prefixed project dirs (``CAN_Bus_Controller_can_tx``)
and collides with generic module names (``test``/``top``). Net effect: every
successful nangate45 recipe sat permanently as ``candidate`` (ab_trials all
sky130hd, which leaves residuals → Tier 1), and "successful solutions get
promoted" silently never happened for nangate45.

The fix adds Tier 2: resolve A/B subjects from fix_trajectories/fix_events, which
record the precise ``project_path`` that hit each ``symptom_id`` — symptom-
confirmed and on-disk-exact, regardless of dir-naming scheme.
"""
import ab_runner
import knowledge_db


def _add_run(conn, *, path, design_name, sid_for_fixhist=None,
             fix_table="fix_trajectories", platform="nangate45", cells=1000):
    """A FIXED design: a clean run row + a fix-history row carrying the symptom,
    and (deliberately) NO run_violations row (Tier 1 must stay empty)."""
    path.mkdir(exist_ok=True)
    rid = f"r_{path.name}"
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, platform, "
        "design_class, cell_count, orfs_status, orfs_fail_stage, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (rid, str(path), design_name, platform, "logic/unknown", cells, "pass",
         None, "2026-06-22T00:00:00Z"))
    if sid_for_fixhist:
        if fix_table == "fix_trajectories":
            conn.execute(
                "INSERT INTO fix_trajectories (fix_session_id, project_path, "
                "design_name, platform, check_type, outcome, symptom_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"s_{path.name}", str(path), design_name, platform, "drc",
                 "resolved", sid_for_fixhist))
        else:
            conn.execute(
                "INSERT INTO fix_events (fix_session_id, project_path, design_name, "
                "platform, check_type, iter, strategy, symptom_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"s_{path.name}", str(path), design_name, platform, "drc", 1,
                 "antenna_diode_repair", sid_for_fixhist))
    return rid


def _conn(tmp_path):
    conn = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(conn)
    sid = "84ffbb174a5ee2b7"
    conn.execute(
        "INSERT OR IGNORE INTO symptoms (symptom_id, check_type, class, "
        "predicates_json, symptom_schema_version, first_seen) VALUES (?,?,?,?,?,?)",
        (sid, "drc", "antenna", "{}", 1, "2026-06-22T00:00:00Z"))
    return conn, sid


def test_successful_recipe_reachable_via_fix_history(tmp_path):
    """The exact campaign signature: two FIXED designs whose project-dir basenames
    differ from their DESIGN_NAME, no run_violations residual. Tier 1 finds nothing;
    the fix-history tier must resolve both and yield a 2-design trial."""
    conn, sid = _conn(tmp_path)
    # repo-prefixed dirs (basename != design_name) — the case the name-list missed.
    _add_run(conn, path=tmp_path / "CAN_Bus_Controller_can_tx",
             design_name="can_tx", sid_for_fixhist=sid, cells=800)
    _add_run(conn, path=tmp_path / "Riscy_SoC_rtl_cpu_mem",
             design_name="mem", sid_for_fixhist=sid, cells=1200)
    conn.commit()

    trial = ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                                 platform="nangate45", strategy="antenna_diode_repair")
    assert trial is not None, "successful recipe must be A/B-reachable via fix history"
    assert trial["match_level"] == "fixhist_platform"
    picked = sorted(d["design_name"] for d in trial["designs"])
    assert picked == ["can_tx", "mem"]
    # cheapest-first ordering preserved
    assert trial["designs"][0]["design_name"] == "can_tx"


def test_fix_events_alone_also_resolves(tmp_path):
    """Some single-iteration fixes write fix_events but no trajectory row; the union
    source must still find them."""
    conn, sid = _conn(tmp_path)
    _add_run(conn, path=tmp_path / "Repo_a_alpha", design_name="alpha",
             sid_for_fixhist=sid, fix_table="fix_events", cells=500)
    _add_run(conn, path=tmp_path / "Repo_b_beta", design_name="beta",
             sid_for_fixhist=sid, fix_table="fix_events", cells=700)
    conn.commit()
    trial = ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                                 platform="nangate45", strategy="antenna_diode_repair")
    assert trial is not None and trial["match_level"] == "fixhist_platform"
    assert len(trial["designs"]) == 2


def test_single_exhibitor_is_honestly_unmatched(tmp_path):
    """Only ONE design ever hit the symptom: a 2-design trial is genuinely
    impossible — plan_trial must return None, not fabricate a second subject."""
    conn, sid = _conn(tmp_path)
    _add_run(conn, path=tmp_path / "Only_One_solo", design_name="solo",
             sid_for_fixhist=sid, cells=900)
    conn.commit()
    assert ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                                platform="nangate45",
                                strategy="antenna_diode_repair") is None
    # ...but a size-1 trial IS satisfiable from the lone exhibitor.
    one = ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                               platform="nangate45", strategy="antenna_diode_repair",
                               n_designs=1)
    assert one is not None and one["designs"][0]["design_name"] == "solo"


def test_fix_history_excludes_arm_dirs(tmp_path):
    """An A/B arm copy carries the symptom too — it must never count toward the
    subject pool. One real design + one arm copy, nothing else: a size-2 trial is
    unsatisfiable (the arm dir does not count), even pooled across platforms."""
    conn, sid = _conn(tmp_path)
    _add_run(conn, path=tmp_path / "Repo_real_design", design_name="design",
             sid_for_fixhist=sid, cells=600)
    _add_run(conn, path=tmp_path / "Repo_real_design_abB_antenna_0",
             design_name="design", sid_for_fixhist=sid, cells=600)
    conn.commit()
    # Only ONE real subject -> size 2 fails (proves the arm dir is not counted)...
    assert ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                                platform="nangate45",
                                strategy="antenna_diode_repair") is None
    # ...while a size-1 trial picks the real design, never the arm copy.
    one = ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                               platform="nangate45", strategy="antenna_diode_repair",
                               n_designs=1)
    assert one is not None
    assert not ab_runner._is_arm_dir(one["designs"][0]["project_path"])


def test_fix_history_never_pools_across_platforms(tmp_path):
    """2026-06-25: plan_trial must NEVER cross platforms — an A/B arm flows at the
    recipe's `platform`, so a sky130hd subject under a nangate45 recipe runs the wrong
    platform and the verdict is meaningless. A nangate45 recipe with only ONE
    same-platform exhibitor returns None (honestly unvalidatable), never reaches for the
    sky130hd design."""
    conn, sid = _conn(tmp_path)
    _add_run(conn, path=tmp_path / "Repo_nan_design", design_name="design",
             sid_for_fixhist=sid, cells=600)
    _add_run(conn, path=tmp_path / "Repo_sky_design", design_name="skyd",
             sid_for_fixhist=sid, platform="sky130hd", cells=650)
    conn.commit()
    # nangate45 alone has 1 exhibitor (<2); the sky130hd one MUST NOT be pooled in.
    assert ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                                platform="nangate45",
                                strategy="antenna_diode_repair") is None
    # ...but a same-platform 2nd exhibitor DOES yield a (same-platform) trial.
    _add_run(conn, path=tmp_path / "Repo_nan_design2", design_name="design2",
             sid_for_fixhist=sid, cells=700)
    conn.commit()
    trial = ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                                 platform="nangate45", strategy="antenna_diode_repair")
    assert trial is not None and trial["match_level"] == "fixhist_platform"
    assert len(trial["designs"]) == 2


# ---- Tier 1 on-disk filter (2026-07-03 ghost-subject regression) ----------------

def _add_viol_run(conn, sid, *, path, design_name, cells, mkdir):
    """A run that EXHIBITS the symptom via run_violations (Tier 1's source)."""
    if mkdir:
        path.mkdir(exist_ok=True)
    rid = f"r_{path.name}"
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, platform, "
        "design_class, cell_count, orfs_status, ingested_at) VALUES (?,?,?,?,?,?,?,?)",
        (rid, str(path), design_name, "sky130hd", "logic/medium", cells, "pass",
         "2026-07-03T00:00:00Z"))
    conn.execute(
        "INSERT INTO run_violations (run_id, platform, symptom_id) VALUES (?,?,?)",
        (rid, "sky130hd", sid))


def test_tier1_skips_wiped_subject_dirs(tmp_path):
    """2026-07-03: Tier 1 (run_violations) was the ONLY subject tier without the
    on-disk `os.path.isdir` filter (Tiers 2/3 have it). After the sky130 clean-slate
    reset wiped the June-17-era `<design>__sky130hd` clone dirs, their immutable
    runs/run_violations history made Tier 1 select GHOST subjects — cheapest-first
    even ranked the tiny wiped clones ahead of real dirs — and plan_arms ledger'd
    arms that could never flow (place_arm_incomplete every drain), starving the
    core_util_relief candidates."""
    conn, sid = _conn(tmp_path)
    ghost = tmp_path / "wiped__sky130hd"                 # NOT created on disk
    _add_viol_run(conn, sid, path=ghost, design_name="wiped", cells=10, mkdir=False)
    _add_viol_run(conn, sid, path=tmp_path / "real_a", design_name="real_a",
                  cells=100, mkdir=True)
    _add_viol_run(conn, sid, path=tmp_path / "real_b", design_name="real_b",
                  cells=200, mkdir=True)
    trial = ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/medium",
                                 platform="sky130hd", strategy="core_util_relief",
                                 n_designs=2)
    assert trial is not None
    paths = [d["project_path"] for d in trial["designs"]]
    assert str(ghost) not in paths, "Tier 1 returned a wiped (non-existent) subject"
    assert len(paths) == 2


def test_tier1_all_ghosts_is_honestly_unmatched(tmp_path):
    """All Tier-1 exhibitors wiped -> plan_trial must fall through / return None,
    never fabricate a trial on ghost dirs."""
    conn, sid = _conn(tmp_path)
    _add_viol_run(conn, sid, path=tmp_path / "g1__sky130hd", design_name="g1",
                  cells=10, mkdir=False)
    _add_viol_run(conn, sid, path=tmp_path / "g2__sky130hd", design_name="g2",
                  cells=20, mkdir=False)
    assert ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/medium",
                                platform="sky130hd", strategy="core_util_relief",
                                n_designs=2) is None
