"""2026-07-04 operator request: timestamps in the SYSTEM timezone, not UTC.

All DB/journal/ledger writers now stamp system-local time WITH the numeric
offset (`2026-07-04T20:41:00-07:00`) — matching the flow artifacts (RUN_* dirs
already used local time). The migration hazard these tests lock down: legacy
rows carry UTC "Z" stamps that sort AHEAD of new local stamps lexicographically
(by the UTC offset), so every load-bearing "latest row per project" ordering
was converted to julianday(), which parses both regimes and orders by REAL time.
"""
import datetime as _dt
import re

import ab_runner
import engineer_loop
import escalations
import journal_db
import knowledge_db
import recipe_lifecycle

_OFFSET_RE = re.compile(r"[+-]\d{2}:\d{2}$")


def test_now_helpers_stamp_local_with_offset():
    for mod in (ab_runner, recipe_lifecycle, escalations, journal_db,
                engineer_loop):
        ts = mod._now()
        assert not ts.endswith("Z"), mod.__name__
        assert _OFFSET_RE.search(ts), f"{mod.__name__}: {ts} lacks a UTC offset"
        # Round-trips as an aware datetime in the system zone.
        parsed = _dt.datetime.fromisoformat(ts)
        assert parsed.utcoffset() is not None


def test_arm_metric_latest_row_survives_regime_mix(tmp_path):
    """A NEW local-stamped row written AFTER an old UTC-stamped row must win the
    latest-row query even though it sorts lexicographically BEFORE it (local tz
    behind UTC). Without julianday ordering, _arm_metric returned the STALE row
    for up to a UTC-offset's worth of hours after the switch — mid-campaign that
    mis-judges arms."""
    conn = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn)
    now = _dt.datetime.now().astimezone()
    old_utc = (now - _dt.timedelta(minutes=30)).astimezone(
        _dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    new_local = now.isoformat(timespec="seconds")
    # Sanity: the hazard is real only when the local zone is behind UTC —
    # lexicographic would then pick the OLD row. julianday must pick the NEW one
    # in EVERY zone.
    conn.execute("INSERT INTO runs (run_id, project_path, ingested_at, "
                 "orfs_status, drc_status) VALUES ('old','/p/arm',?, 'fail','fail')",
                 (old_utc,))
    conn.execute("INSERT INTO runs (run_id, project_path, ingested_at, "
                 "orfs_status, drc_status, lvs_status, rcx_status) "
                 "VALUES ('new','/p/arm',?, 'pass','clean','clean','complete')",
                 (new_local,))
    conn.commit()
    m = engineer_loop._arm_metric(conn, "/p/arm")
    assert m["is_success"] is True          # the NEW (clean) row won


def test_ab_subject_row_number_survives_regime_mix(tmp_path):
    """ab_runner's latest-row-per-project window (Tier 2/3 subject resolution)
    must also order by real time across the regime mix."""
    conn = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn)
    proj = tmp_path / "design_x"
    proj.mkdir()
    now = _dt.datetime.now().astimezone()
    old_utc = (now - _dt.timedelta(minutes=30)).astimezone(
        _dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    new_local = now.isoformat(timespec="seconds")
    conn.execute("INSERT INTO runs (run_id, project_path, ingested_at, platform, "
                 "design_name, cell_count) VALUES ('old',?,?,'sky130hd','x', 999)",
                 (str(proj), old_utc))
    conn.execute("INSERT INTO runs (run_id, project_path, ingested_at, platform, "
                 "design_name, cell_count) VALUES ('new',?,?,'sky130hd','x', 5)",
                 (str(proj), new_local))
    conn.execute("INSERT INTO fix_trajectories (fix_session_id, project_path, "
                 "check_type, symptom_id) VALUES ('s1', ?, 'drc', 'sym1')",
                 (str(proj),))
    conn.commit()
    subs = ab_runner._symptom_designs(conn, "sym1", "sky130hd")
    assert subs and subs[0]["cell_count"] == 5   # rn=1 is the NEW row
