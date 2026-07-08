"""check_timing.py --journal appends a timing fix_event line to fix_log.jsonl."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
CHECK_TIMING = SKILL / "scripts" / "reports" / "check_timing.py"


def _journal(tmp_path, before_obj, after_obj, strategy="period_relax"):
    """Run check_timing.py --journal with the given before/after timing_check.json
    contents and return the parsed fix_log rows."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    before = proj / "reports" / "before.json"
    after = proj / "reports" / "after.json"
    before.write_text(json.dumps(before_obj))
    after.write_text(json.dumps(after_obj))
    subprocess.run(["python3", str(CHECK_TIMING), "--journal",
                    "--project", str(proj), "--before", str(before),
                    "--after", str(after), "--strategy", strategy], check=True)
    return [json.loads(l) for l in
            (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]


def test_journal_cleared_when_after_clean(tmp_path):
    # Real timing_check.json schema: keys 'wns'/'tier'/'clock_period' (NOT *_ns).
    rows = _journal(
        tmp_path,
        {"tier": "moderate", "wns": -3.0, "clock_period": 10.0},
        {"tier": "clean", "wns": 0.1, "clock_period": 13.0},
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["check"] == "timing" and r["strategy"] == "period_relax"
    assert r["violation_class"] == "moderate"     # the before tier
    assert r["verdict"] == "cleared"              # after tier == clean
    assert r["before"] == 3.0                     # abs(before wns)
    assert r["after"] == 0.1                      # abs(after wns)
    assert json.loads(r["cumulative_config"])["clock_period_ns"] == 13.0
    assert r["fix_session_id"]


def test_journal_emits_predicates_and_config_delta(tmp_path):
    # Symptom-indexed memory parity: timing fix_events carry config_delta + predicates.
    rows = _journal(
        tmp_path,
        {"tier": "minor", "wns": -0.5, "clock_period": 10},
        {"tier": "clean", "wns": 0.1, "clock_period": 11},
    )
    assert len(rows) == 1
    r = rows[0]
    assert "predicates" in r and isinstance(r["predicates"], dict)
    assert json.loads(r["config_delta"]) == {"clock_period_ns": 11}


def test_journal_minor_is_win_not_cleared(tmp_path):
    # moderate (wns -3.0) -> minor (wns -1.0): improved but NOT closed (wns<0).
    rows = _journal(
        tmp_path,
        {"tier": "moderate", "wns": -3.0, "clock_period": 10.0},
        {"tier": "minor", "wns": -1.0, "clock_period": 11.5},
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["violation_class"] == "moderate"
    assert r["verdict"] == "win"                  # minor still means WNS<0
    assert r["before"] == 3.0
    assert r["after"] == 1.0


def test_journal_regression_when_worse(tmp_path):
    # after WNS magnitude worse than before -> regression.
    rows = _journal(
        tmp_path,
        {"tier": "moderate", "wns": -2.0, "clock_period": 10.0},
        {"tier": "moderate", "wns": -3.0, "clock_period": 10.0},
    )
    r = rows[0]
    assert r["verdict"] == "regression"
    assert r["before"] == 2.0
    assert r["after"] == 3.0


def test_journal_no_change_when_equal(tmp_path):
    rows = _journal(
        tmp_path,
        {"tier": "minor", "wns": -1.0, "clock_period": 10.0},
        {"tier": "minor", "wns": -1.0, "clock_period": 10.0},
    )
    assert rows[0]["verdict"] == "no_change"


def test_journal_legacy_keys_fallback(tmp_path):
    # Legacy *_ns keys still read via fallback (older artifacts).
    rows = _journal(
        tmp_path,
        {"tier": "moderate", "wns_ns": -3.0, "clock_period_ns": 10.0},
        {"tier": "clean", "wns_ns": 0.1, "clock_period_ns": 13.0},
    )
    r = rows[0]
    assert r["verdict"] == "cleared"
    assert r["before"] == 3.0
    assert r["after"] == 0.1
    assert json.loads(r["cumulative_config"])["clock_period_ns"] == 13.0
