"""Guards tools/check_ledger_signoff_backed.py — the ledger-signoff "bug-#7" gate.

Pins the exact regressions that let the OLD inline `LIKE '%basename'` gate cry wolf
on ~197/593 designs after the 2026-07-07 store union while masking ~500 real gaps:
  * exact-path join (underscore is NOT a wildcard)
  * platform scoping (a nangate45 row never "backs" a sky130hd clean)
  * three-way split: backed / fabricated (ALARM) / not_ingested (WARN)
See references/failure-patterns.md "Ledger-signoff gate mis-join".
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import check_ledger_signoff_backed as gate  # noqa: E402


def _mk_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE runs (run_id TEXT, project_path TEXT, platform TEXT, "
                "ingested_at TEXT, drc_status TEXT, lvs_status TEXT)")
    con.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _mk_ledger(path, entries):
    with open(path, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _mk_reports(root, design, drc, lvs):
    d = root / "design_cases" / design / "reports"
    d.mkdir(parents=True, exist_ok=True)
    if drc is not None:
        (d / "drc.json").write_text(json.dumps({"status": drc}))
    if lvs is not None:
        (d / "lvs.json").write_text(json.dumps({"status": lvs}))


def _pp(root, design):
    return str(root / "design_cases" / design)


@pytest.fixture
def env(tmp_path):
    root = tmp_path
    (root / "design_cases" / "_batch").mkdir(parents=True)
    return root


def test_backed_exact_platform_scoped(env):
    """A ledger-clean design with a fresh sky130hd clean run is `backed`."""
    db = env / "k.sqlite"
    _mk_db(db, [("r1", _pp(env, "good_design"), "sky130hd", "2026-07-02T00:00:00Z", "clean", "clean")])
    ledger = env / "design_cases/_batch/sky130hd_campaign.jsonl"
    _mk_ledger(ledger, [{"design": "good_design", "project_path": _pp(env, "good_design"),
                         "state": "clean", "kind": "normal"}])
    con = sqlite3.connect(db)
    res = gate.classify(gate._load_ledger(ledger), con, "sky130hd", env)
    con.close()
    assert res["backed"] == ["good_design"]
    assert res["fabricated"] == [] and res["not_ingested"] == []


def test_fabricated_knowledge_nonclean_and_no_disk_is_alarm(env):
    """knowledge lvs=mismatch AND no on-disk clean evidence -> fabricated (ALARM)."""
    db = env / "k.sqlite"
    _mk_db(db, [("r1", _pp(env, "liar"), "sky130hd", "2026-07-02T00:00:00Z", "clean", "mismatch")])
    ledger = env / "design_cases/_batch/sky130hd_campaign.jsonl"
    _mk_ledger(ledger, [{"design": "liar", "project_path": _pp(env, "liar"),
                         "state": "clean", "kind": "normal"}])
    rc = gate.main(["--platform", "sky130hd", "--ledger", str(ledger),
                    "--db", str(db), "--root", str(env)])
    assert rc == 1


def test_stale_knowledge_but_ondisk_clean_is_not_ingested(env):
    """knowledge's latest run is a stale mismatch, but on-disk reports are clean.

    This is the APB_Based_GPIO / Canakari case: an intermediate mismatch was ingested
    then the design re-signed-off clean on disk without a re-ingest. On-disk truth wins
    -> not_ingested(stale_knowledge), NOT fabricated; the gate must NOT fail (exit 0).
    """
    db = env / "k.sqlite"
    _mk_db(db, [("r1", _pp(env, "stale"), "sky130hd", "2026-07-02T04:23:00Z", "clean", "mismatch")])
    _mk_reports(env, "stale", "clean", "clean")
    ledger = env / "design_cases/_batch/sky130hd_campaign.jsonl"
    _mk_ledger(ledger, [{"design": "stale", "project_path": _pp(env, "stale"),
                         "state": "clean", "kind": "normal"}])
    con = sqlite3.connect(db)
    res = gate.classify(gate._load_ledger(ledger), con, "sky130hd", env)
    con.close()
    assert res["fabricated"] == []
    assert [(n, note) for n, _d, _l, note in res["not_ingested"]] == [("stale", "stale_knowledge")]
    assert gate.main(["--ledger", str(ledger), "--db", str(db), "--root", str(env)]) == 0


def test_not_ingested_ondisk_clean_is_warn_not_alarm(env):
    """No knowledge run, but on-disk reports are clean -> not_ingested (WARN, exit 0)."""
    db = env / "k.sqlite"
    _mk_db(db, [])  # empty knowledge
    _mk_reports(env, "on_disk_only", "clean", "clean")
    ledger = env / "design_cases/_batch/sky130hd_campaign.jsonl"
    _mk_ledger(ledger, [{"design": "on_disk_only", "project_path": _pp(env, "on_disk_only"),
                         "state": "clean", "kind": "normal"}])
    con = sqlite3.connect(db)
    res = gate.classify(gate._load_ledger(ledger), con, "sky130hd", env)
    con.close()
    assert [n for n, *_ in res["not_ingested"]] == ["on_disk_only"]
    assert res["fabricated"] == []
    # WARN must NOT fail the gate.
    assert gate.main(["--ledger", str(ledger), "--db", str(db), "--root", str(env)]) == 0


def test_no_run_no_disk_is_fabricated_alarm(env):
    """Ledger clean with no knowledge run AND no on-disk evidence -> fabricated (ALARM)."""
    db = env / "k.sqlite"
    _mk_db(db, [])
    ledger = env / "design_cases/_batch/sky130hd_campaign.jsonl"
    _mk_ledger(ledger, [{"design": "ghost", "project_path": _pp(env, "ghost"),
                         "state": "clean", "kind": "normal"}])
    assert gate.main(["--ledger", str(ledger), "--db", str(db), "--root", str(env)]) == 1


def test_underscore_is_not_a_wildcard(env):
    """OLD gate's `LIKE '%a_b'` also matched `aXb`; exact join must not.

    knowledge only has a clean run for the DIFFERENT design `aXb`; the ledger-clean
    `a_b` has no run of its own and no on-disk reports -> fabricated, NOT backed.
    """
    db = env / "k.sqlite"
    _mk_db(db, [("r1", _pp(env, "aXb"), "sky130hd", "2026-07-02T00:00:00Z", "clean", "clean")])
    ledger = env / "design_cases/_batch/sky130hd_campaign.jsonl"
    _mk_ledger(ledger, [{"design": "a_b", "project_path": _pp(env, "a_b"),
                         "state": "clean", "kind": "normal"}])
    con = sqlite3.connect(db)
    res = gate.classify(gate._load_ledger(ledger), con, "sky130hd", env)
    con.close()
    assert res["backed"] == []
    assert [e[0] for e in res["fabricated"]] == ["a_b"]


def test_cross_platform_run_does_not_back_a_clean(env):
    """A nangate45 clean run at the exact path must NOT back a sky130hd ledger-clean.

    This is the DMA_Controller false-positive/false-negative that broke the old gate.
    """
    db = env / "k.sqlite"
    _mk_db(db, [("r1", _pp(env, "cross"), "nangate45", "2026-07-06T00:00:00Z", "clean", "clean")])
    _mk_reports(env, "cross", "clean", "clean")  # on-disk sky130 result present but un-ingested
    ledger = env / "design_cases/_batch/sky130hd_campaign.jsonl"
    _mk_ledger(ledger, [{"design": "cross", "project_path": _pp(env, "cross"),
                         "state": "clean", "kind": "normal"}])
    con = sqlite3.connect(db)
    res = gate.classify(gate._load_ledger(ledger), con, "sky130hd", env)
    con.close()
    # nangate45 run ignored -> falls through to on-disk -> not_ingested, never backed.
    assert res["backed"] == []
    assert [n for n, *_ in res["not_ingested"]] == ["cross"]


def test_stale_suffixed_variant_run_does_not_back_current_claim(env):
    """A prior-round `<path>__sky130hd` run must NOT back the plain-path July claim.

    Consulting it over-credited ~385 stale June cleans as "backed" and false-alarmed
    ~12 null June runs. Only the plain path's own run / on-disk reports may back it, so
    a design whose ONLY knowledge run is at the suffixed path (no plain run, no on-disk
    reports) is fabricated, never backed.
    """
    db = env / "k.sqlite"
    _mk_db(db, [("r1", _pp(env, "variant") + "__sky130hd", "sky130hd",
                 "2026-06-17T00:00:00Z", "clean", "clean")])
    ledger = env / "design_cases/_batch/sky130hd_campaign.jsonl"
    _mk_ledger(ledger, [{"design": "variant", "project_path": _pp(env, "variant"),
                         "state": "clean", "kind": "normal"}])
    con = sqlite3.connect(db)
    res = gate.classify(gate._load_ledger(ledger), con, "sky130hd", env)
    con.close()
    assert res["backed"] == []
    assert [e[0] for e in res["fabricated"]] == ["variant"]


def test_ab_arm_and_nonclean_rows_skipped(env):
    """ab_arm subjects and non-clean ledger rows are not signoff-gated."""
    db = env / "k.sqlite"
    _mk_db(db, [])
    ledger = env / "design_cases/_batch/sky130hd_campaign.jsonl"
    _mk_ledger(ledger, [
        {"design": "arm", "project_path": _pp(env, "arm"), "state": "clean", "kind": "ab_arm"},
        {"design": "esc", "project_path": _pp(env, "esc"), "state": "escalated", "kind": "normal"},
    ])
    con = sqlite3.connect(db)
    res = gate.classify(gate._load_ledger(ledger), con, "sky130hd", env)
    con.close()
    assert res == {"backed": [], "fabricated": [], "not_ingested": []}
