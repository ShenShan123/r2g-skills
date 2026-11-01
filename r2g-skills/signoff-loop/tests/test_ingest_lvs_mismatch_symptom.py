"""A netgen LVS verdict ("mismatch") must key the run under its LVS symptom, like
the KLayout path's "fail" -- otherwise LVS recipes never find A/B subjects."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "knowledge"))
import ingest_run  # noqa: E402
import knowledge_db  # noqa: E402
import symptom  # noqa: E402


def _row(status):
    conn = sqlite3.connect(":memory:")
    knowledge_db.ensure_schema(conn)
    ingest_run._write_run_violations(conn, "r1", "fam", "gf180", {"status": "clean"},
                                     {"status": status, "mismatch_class": "top_pin_mismatch"},
                                     {"tier": "clean"}, 0.1)
    return conn.execute("SELECT symptom_id, signature_json FROM run_violations").fetchone()


def test_netgen_mismatch_and_klayout_fail_share_the_lvs_symptom():
    want = symptom.symptom_id(symptom.canonical_signature("lvs", "top_pin_mismatch", {}))
    assert _row("mismatch")[0] == want == _row("fail")[0]


def test_clean_lvs_still_falls_through_to_timing():
    assert '"check": "timing"' in _row("clean")[1]
