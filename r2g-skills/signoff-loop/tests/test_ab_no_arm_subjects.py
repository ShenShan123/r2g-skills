"""plan_trial must never pick an A/B arm dir as a SUBJECT.

Arm copies (<name>_ab{A,B}_<strat8>_<r>) get ingested and carry the recipe's
symptom, so without a guard plan_trial re-selects them — copying into ever-deeper
_abA_..._abA_... nests and polluting run_violations. Subjects must be real designs.
"""
import ab_runner
import knowledge_db


def test_is_arm_dir():
    assert ab_runner._is_arm_dir("/x/spi_controller__sky130hd_abA_density__0")
    assert ab_runner._is_arm_dir("/x/foo_abB_route_re_1")
    assert not ab_runner._is_arm_dir("/x/spi_controller__sky130hd")
    assert not ab_runner._is_arm_dir("/x/my_label_design")  # 'lab' != 'abA/abB'


def test_plan_trial_excludes_arm_dirs(tmp_path):
    conn = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(conn)
    sid = "11f02dbb19046bb6"
    conn.execute("INSERT OR IGNORE INTO symptoms (symptom_id, check_type, class, "
                 "predicates_json, symptom_schema_version, first_seen) "
                 "VALUES (?,?,?,?,?,?)",
                 (sid, "orfs_stage", "route", "{}", 1, "2026-06-17T00:00:00Z"))
    # one REAL route-fail subject + two arm-dir copies, all carrying the symptom
    for nm, path in (("real", tmp_path / "des_area__sky130hd"),
                     ("real", tmp_path / "des_area__sky130hd_abA_route_re_0"),
                     ("real", tmp_path / "des_area__sky130hd_abB_route_re_0")):
        path.mkdir(exist_ok=True)
        rid = f"r_{path.name}"
        conn.execute("INSERT INTO runs (run_id, project_path, design_name, platform, "
                     "design_class, cell_count, orfs_status, orfs_fail_stage, ingested_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (rid, str(path), nm, "sky130hd", "logic/unknown", 3000, "fail",
                      "route", "2026-06-17T00:00:00Z"))
        conn.execute("INSERT INTO run_violations (run_id, symptom_id, platform, "
                     "design_family, snapshot_ts) VALUES (?,?,?,?,?)",
                     (rid, sid, "sky130hd", nm, "2026-06-17T00:00:00Z"))
    conn.commit()

    # Only ONE real subject exists (the 2 arm dirs must be excluded), so a trial of
    # size 1 is satisfiable and a trial of size 2 is NOT (proving the arm dirs are
    # not counted toward the subject pool).
    trial = ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                                 platform="sky130hd", strategy="route_relief", n_designs=1)
    assert trial is not None
    picked = [d["project_path"] for d in trial["designs"]]
    assert all(not ab_runner._is_arm_dir(p) for p in picked), picked
    assert picked == [str(tmp_path / "des_area__sky130hd")]

    assert ab_runner.plan_trial(conn, symptom_id=sid, design_class="logic/unknown",
                                platform="sky130hd", strategy="route_relief",
                                n_designs=2) is None
