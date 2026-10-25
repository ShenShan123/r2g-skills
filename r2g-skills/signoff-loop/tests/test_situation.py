"""Situation signature (knowledge/situation.py, sit-v1): the pre-fix failure context
stored ALONGSIDE symptom_id (R2G memory redesign A2, 2026-10-01).

Every backend route abort shares one symptom, whether it is a GRT-0116 congestion
abort on an auto-sized die at 8% utilisation or a detailed-route timeout on a fixed
die; the situation separates them so learned evidence stops averaging over fixes
that only work in one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import ingest_run
import knowledge_db
import situation

SKILL = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path, *, util="8", die_area=None, stage_status=2,
             err="[ERROR GRT-0116] Global routing finished with congestion.") -> Path:
    proj = tmp_path / "chacha_core"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    cfg = "export DESIGN_NAME = chacha\nexport PLATFORM = sky130hs\n"
    cfg += f"export DIE_AREA = {die_area}\n" if die_area else f"export CORE_UTILIZATION = 30\n"
    # the signoff-fix auto block overrides the base value (last assignment wins)
    cfg += ("# >>> r2g signoff-fix (auto) >>>\n"
            f"export CORE_UTILIZATION = {util}\n# <<< r2g signoff-fix (auto) <<<\n")
    (proj / "constraints" / "config.mk").write_text(cfg)
    run = proj / "backend" / "RUN_1"
    run.mkdir(parents=True)
    (run / "stage_log.jsonl").write_text(
        json.dumps({"stage": "cts", "status": 0}) + "\n"
        + json.dumps({"stage": "route", "status": stage_status}) + "\n")
    (run / "flow.log").write_text(f"routing...\n{err}\nERROR: Stage 'route' failed\n")
    return proj


def test_bands_and_route_keying():
    s = situation.canonical("route", platform="sky130hs", die_mode="auto", util="8",
                            error_code="GRT-0116")
    assert s["check"] == "orfs_stage" and s["violation_class"] == "route"
    assert s["util_band"] == "le12" and s["count_band"] is None
    assert situation.util_band(13) == "13-30" and situation.util_band(61) == "gt60"
    assert situation.count_band(6) == "6-20" and situation.count_band(0) is None
    # a fixed die has no utilisation band; a signoff check has no error code
    assert situation.canonical("drc", die_mode="fixed", util="40")["util_band"] is None
    assert situation.canonical("drc", error_code="X-1")["error_code"] is None
    # stable, content-addressed id
    assert situation.situation_id(s) == situation.situation_id(dict(s))
    assert situation.situation_id(s) != situation.situation_id({**s, "util_band": "13-30"})


def test_from_project_reads_abort_code_die_mode_and_auto_block(tmp_path):
    s = situation.from_project(_project(tmp_path), "route")
    assert s == {"v": "sit-v2", "check": "orfs_stage", "violation_class": "route",
                 "error_code": "GRT-0116", "timeout": False, "platform": "sky130hs",
                 "die_mode": "auto", "util_band": "le12", "count_band": None,
                 "perimeter_band": None}


def test_timeout_and_fixed_die_are_distinct_situations(tmp_path):
    a = situation.from_project(_project(tmp_path / "a"), "route")
    b = situation.from_project(_project(tmp_path / "b", stage_status=124, err="",
                                        die_area="0 0 500 500"), "route")
    assert b["timeout"] is True and b["error_code"] is None and b["die_mode"] == "fixed"
    assert situation.situation_id(a) != situation.situation_id(b)


def test_snapshot_cli_prints_json(tmp_path, capsys):
    proj = _project(tmp_path)
    assert situation.main(["snapshot", str(proj), "--check", "drc", "--vclass", "li.3",
                           "--before", "6"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["violation_class"] == "li.3" and s["count_band"] == "6-20"


def _fix_events(conn):
    return conn.execute("SELECT strategy, situation_id, situation_json, situation_source "
                        "FROM fix_events ORDER BY strategy").fetchall()


def test_ingest_stores_snapshot_and_derives_absent(tmp_knowledge_dir, tmp_path):
    proj = _project(tmp_path)
    snap = situation.canonical("orfs_stage", violation_class="route", error_code="GRT-0116",
                               platform="sky130hs", die_mode="auto", util="8")
    rows = [
        {"fix_session_id": "s1", "iter": 1, "strategy": "route_relief", "check": "route",
         "violation_class": "route", "before": 1, "after": 1, "verdict": "no_improvement",
         "situation": snap},
        {"fix_session_id": "s1", "iter": 2, "strategy": "core_util_relief",
         "check": "route", "violation_class": "route", "before": 1, "after": 1,
         "verdict": "no_improvement"},
    ]
    (proj / "reports" / "fix_log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run._ingest_fix_events(conn, proj, "chacha", "chacha", "sky130hs")
    got = {r[0]: r for r in _fix_events(conn)}
    assert got["route_relief"][1] == situation.situation_id(snap)
    assert got["route_relief"][3] == "snapshot"
    assert got["core_util_relief"][3] == "ingest"
    assert json.loads(got["core_util_relief"][2])["error_code"] == "GRT-0116"
    assert conn.execute("SELECT count(*) FROM situations").fetchone()[0] >= 1
    # symptom_id is untouched by the situation (additive, no re-keying)
    assert conn.execute("SELECT count(DISTINCT symptom_id) FROM fix_events").fetchone()[0] == 1

    # re-ingest after the project config changed (post-fix): the snapshot row keeps
    # its pre-fix situation; the derived row follows the new config.
    cfg = proj / "constraints" / "config.mk"
    cfg.write_text(cfg.read_text().replace("CORE_UTILIZATION = 8", "CORE_UTILIZATION = 40"))
    ingest_run._ingest_fix_events(conn, proj, "chacha", "chacha", "sky130hs")
    got2 = {r[0]: r for r in _fix_events(conn)}
    assert got2["route_relief"][1] == got["route_relief"][1]
    assert json.loads(got2["core_util_relief"][2])["util_band"] == "31-60"
    conn.close()


def test_engineer_loop_recovery_row_carries_situation(tmp_path):
    sys.path.insert(0, str(SKILL / "scripts" / "loop"))
    import engineer_loop
    proj = _project(tmp_path)
    sit = situation.from_project(proj, "orfs_stage", "place")
    engineer_loop._record_resize_fix({"project_path": str(proj)}, cleared=True, situation_=sit)
    row = json.loads((proj / "reports" / "fix_log.jsonl").read_text().splitlines()[-1])
    assert row["situation"] == sit and row["strategy"] == "core_util_relief"


def test_pin_pressure_separates_ppl0024_designs(tmp_path):
    """sit-v2 (Phase D amendment D-A2): eth_demux needs 2.2x its die perimeter, udp_ip_rx_64
    1.5x — every other field equal, so without this band memory treated them alike."""
    msg = "[ERROR PPL-0024] Number of IO pins (721) exceeds maximum number of available " \
          "positions (394). Increase the die perimeter from 444.62um to 980.56um."
    eth = situation.from_project(_project(tmp_path / "e", err=msg), "orfs_stage", "place")
    msg2 = msg.replace("444.62um to 980.56um", "698.58um to 1045.84um")
    udp = situation.from_project(_project(tmp_path / "u", err=msg2), "orfs_stage", "place")
    assert eth["error_code"] == udp["error_code"] == "PPL-0024"
    assert eth["perimeter_band"] == "1.75-2.5" and udp["perimeter_band"] == "1.25-1.75"
    assert situation.situation_id(eth) != situation.situation_id(udp)
    # other aborts carry no band
    assert situation.from_project(_project(tmp_path / "g"), "route")["perimeter_band"] is None


def test_sit_v1_snapshots_remain_readable():
    v1 = {"v": "sit-v1", "check": "drc", "violation_class": "li.3"}
    assert situation.from_fix_log_row({"situation": v1}) == (v1, "snapshot")
