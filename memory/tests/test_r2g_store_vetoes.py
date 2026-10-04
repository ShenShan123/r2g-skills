"""R2G evidence through a real (temporary) TEHM store: capture -> crystallise -> vetoes.

B5: a strategy that verifiably failed in one situation on >= 2 designs is vetoed
there and only there. B2/B3 on the stored path: rules crystallised from measured
R2G episodes carry per-knob deltas and situation preconditions, and the V1
witness replay accepts the situation slots. B7: rebuild never promotes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tehm_backend import TehmMemoryBackend

SIT = {"v": "sit-v1", "check": "drc", "violation_class": "li.3", "error_code": None,
       "timeout": False, "platform": "sky130hs", "die_mode": "auto",
       "util_band": "le12", "count_band": "1-5"}
OTHER = {**SIT, "violation_class": "m3.2"}


def _project(root: Path, design: str, rows: list[dict]) -> Path:
    proj = root / design
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {design}\nexport PLATFORM = sky130hs\n"
        "export CORE_UTILIZATION = 12\n")
    (proj / "reports" / "fix_log.jsonl").write_text(
        "".join(json.dumps({"fix_session_id": f"{design}_s", "iter": i + 1, **r}) + "\n"
                for i, r in enumerate(rows)))
    return proj


def _fix(strategy, before, after, verdict, sit, util_before="12", util_after="8"):
    return {"check": "drc", "strategy": strategy, "violation_class": sit["violation_class"],
            "from_stage": "floorplan", "before": before, "after": after, "verdict": verdict,
            "config_delta": {"CORE_UTILIZATION": util_after},
            "config_before": {"CORE_UTILIZATION": util_before}, "situation": sit}


@pytest.fixture
def backend(tmp_path):
    b = TehmMemoryBackend(db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "art")
    yield b
    b.close()


def test_veto_needs_two_designs_and_is_situation_scoped(tmp_path, backend):
    backend.ingest_project(_project(tmp_path, "sha_a", [
        _fix("density_relief", 4, 6, "regression", SIT)]))
    assert backend.situation_vetoes(SIT) == {}           # one design: not yet
    backend.ingest_project(_project(tmp_path, "sha_b", [
        _fix("density_relief", 4, 4, "no_improvement", SIT, "17", "9")]))
    v = backend.situation_vetoes(SIT)
    assert set(v) == {"density_relief"} and v["density_relief"]["failures"] == 2
    assert len(v["density_relief"]["designs"]) == 2
    assert backend.situation_vetoes(OTHER) == {}         # another situation: no veto
    assert backend.situation_vetoes(None) == {}


def test_successes_outweigh_failures_no_veto(tmp_path, backend):
    for d, (b, a, verdict) in {"x1": (4, 6, "regression"), "x2": (4, 4, "no_improvement"),
                               "x3": (4, 0, "cleared"), "x4": (6, 0, "cleared"),
                               "x5": (5, 0, "cleared")}.items():
        backend.ingest_project(_project(tmp_path, d, [_fix("density_relief", b, a, verdict, SIT)]))
    assert backend.situation_vetoes(SIT) == {}


def test_crystallised_r2g_rule_carries_delta_and_preconditions_never_promoted(tmp_path, backend):
    for d, (ub, ua) in {"d1": ("12", "8"), "d2": ("11", "7"), "d3": ("10", "6")}.items():
        backend.ingest_project(_project(tmp_path, d, [
            _fix("density_relief", 4, 0, "cleared", SIT, ub, ua)]))
    backend.rebuild()
    conn, _ = backend._open()
    rows = conn.execute("SELECT rule_id, after_pattern_json, hard_preconditions_json, "
                        "validity_status FROM tehm_rules").fetchall()
    flow = [r for r in rows if "rewrite.knob.CORE_UTILIZATION.delta" in r[1]]
    assert flow, [r[1] for r in rows]
    after = json.loads(flow[0][1])
    assert after["rewrite.knob.CORE_UTILIZATION.delta"] == "-4"
    pre = set(json.loads(flow[0][2]))
    assert {"situation.violation_class==li.3", "situation.util_band==le12"} <= pre
    assert flow[0][3] not in ("REJECT_UNFAITHFUL",)      # V1 replay accepts situation slots
    # B7: crystallisation alone never promotes — only the authority path may.
    promoted = conn.execute("SELECT count(*) FROM tehm_rule_status "
                            "WHERE status='promoted'").fetchone()[0]
    assert promoted == 0


def test_situated_rule_is_retrievable_end_to_end_only_in_trial_mode(tmp_path):
    """Through the REAL index + retrieval + activation path (the D-A1 precondition-
    encoding bug made every situated rule an integrity error here)."""
    import runtime_router
    for d, (ub, ua) in {"e1": ("12", "8"), "e2": ("11", "7"), "e3": ("10", "6")}.items():
        b0 = TehmMemoryBackend(db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "art")
        b0.ingest_project(_project(tmp_path, d, [_fix("density_relief", 4, 0, "cleared", SIT, ub, ua)]))
        b0.rebuild()
        b0.close()
    cfg = {"CORE_UTILIZATION": "12", "PLATFORM": "sky130hs"}
    kw = dict(project_dir=tmp_path, check="drc", design_id="new", platform="sky130hs",
              cfg=cfg, reports={}, situation=SIT)
    live = TehmMemoryBackend(db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "art",
                             trial_candidates=False)
    assert runtime_router.signoff_strategies(backend=live, **kw) == []      # nothing promoted
    live.close()
    trial = TehmMemoryBackend(db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "art",
                              trial_candidates=True)
    st = runtime_router.signoff_strategies(backend=trial, **kw)
    assert st and st[0]["config_edits"] == {"CORE_UTILIZATION": "8"}       # 12 + delta -4
    assert st[0]["memory_trial"] is True and st[0]["transformation_family"] == "DENSITY_RELIEF"
    # another situation: the precondition makes it inapplicable
    assert runtime_router.signoff_strategies(
        backend=trial, **{**kw, "situation": {**SIT, "violation_class": "m3.2"}}) == []
    trial.close()


def test_knob_subset_rule_from_differently_shaped_fixes(tmp_path):
    """D-A2: two designs closed the same route situation with DIFFERENT knob sets (no
    ordinary group); a rule over the shared subset forms, marked as a projection, and
    is retrievable in trial mode."""
    import runtime_router
    route = {**SIT, "check": "orfs_stage", "violation_class": "route", "error_code": "GRT-0116",
             "util_band": "gt60", "count_band": None}

    def fix(edits, before):
        return {"check": "orfs_stage", "strategy": "llm_edit:" + ",".join(sorted(edits)),
                "violation_class": "route", "from_stage": "floorplan", "before": 1, "after": 0,
                "verdict": "cleared", "config_delta": edits, "config_before": before,
                "situation": route}
    b = TehmMemoryBackend(db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "art",
                          trial_candidates=True)
    b.ingest_project(_project(tmp_path, "chacha", [fix(
        {"CORE_UTILIZATION": "45", "GPL_ROUTABILITY_DRIVEN": "1", "ROUTING_LAYER_ADJUSTMENT": "0.2"},
        {"CORE_UTILIZATION": "70", "GPL_ROUTABILITY_DRIVEN": "0", "ROUTING_LAYER_ADJUSTMENT": "0"})]))
    b.ingest_project(_project(tmp_path, "des", [fix(
        {"CORE_UTILIZATION": "50", "GPL_ROUTABILITY_DRIVEN": "1", "MAX_ROUTING_LAYER": "met4"},
        {"CORE_UTILIZATION": "70", "GPL_ROUTABILITY_DRIVEN": "0", "MAX_ROUTING_LAYER": "met5"})]))
    b.rebuild()
    conn, _ = b._open()
    subset = [r for r in conn.execute("SELECT before_pattern_json, context_profile_json, "
                                      "validity_status FROM tehm_rules").fetchall()
              if json.loads(r[0]).get("knobs") == "CORE_UTILIZATION,GPL_ROUTABILITY_DRIVEN"]
    assert subset, "no knob-subset rule"
    assert subset[0][2] in ("PROVISIONAL_VALID", "VALIDATED"), subset[0][2]
    assert json.loads(subset[0][1])["knob_projection"] == "CORE_UTILIZATION,GPL_ROUTABILITY_DRIVEN"
    st = runtime_router.signoff_strategies(
        backend=b, project_dir=tmp_path, check="route", design_id="sha", platform="sky130hs",
        cfg={"CORE_UTILIZATION": "70", "GPL_ROUTABILITY_DRIVEN": "0"}, reports={}, situation=route)
    # util deltas -25 / -20 differ -> median -22.5 applied to 70; GPL shared -> concrete
    got = [s for s in st if s["transformation_family"].startswith("KNOB_SUBSET_")]
    assert got and got[0]["config_edits"] == {"CORE_UTILIZATION": "47.5",
                                              "GPL_ROUTABILITY_DRIVEN": "1"}
    assert got[0]["witness_selected"] == ["CORE_UTILIZATION"] and got[0]["memory_trial"]
    b.close()


def test_open_backend_keeps_its_lock_so_others_never_delete_the_wal(tmp_path, monkeypatch):
    """A process with the TEHM backend open must keep its SQLite lock: a second
    process closing its own connection must NOT checkpoint-and-delete the WAL under
    it. snapshot() used to read the DB file through a second descriptor, whose close
    dropped this process's POSIX locks (Phase D2 smoke 2: 'database disk image is
    malformed' in engineer_loop's write-back rebuild)."""
    import subprocess
    import sys as _sys
    import factory
    db = tmp_path / "tehm.sqlite"
    monkeypatch.setenv("TEHM_DB", str(db))
    monkeypatch.setenv("TEHM_ARTIFACTS_ROOT", str(tmp_path / "art"))
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    factory.reset()
    b = factory.open_memory_backend()          # opens + snapshot() (the old lock-dropping path)
    try:
        b.snapshot()
        assert (tmp_path / "tehm.sqlite-wal").exists()
        code = ("import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
                "c.execute('PRAGMA journal_mode=WAL'); "
                "c.execute('CREATE TABLE IF NOT EXISTS t(x)'); c.execute('INSERT INTO t VALUES (1)'); "
                "c.commit(); c.close()")
        subprocess.run([_sys.executable, "-c", code, str(db)], check=True)
        assert (tmp_path / "tehm.sqlite-wal").exists(), "WAL deleted under a live connection"
        b.rebuild()                              # the old failure point
    finally:
        b.close()
        factory.reset()


def test_h5_accepts_a_rule_rejected_at_v2():
    """audit_rule stops at V2 on REJECT_DEGENERATE (V1 is never consulted); H5 used to
    flag every such persisted rule, making any store with one 'unhealthy'."""
    import sqlite3
    from tehm.honesty import h5_validity_order
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE tehm_rules (rule_id TEXT, validity_status TEXT, validity_profile_json TEXT)")
    rows = [("r_deg", "REJECT_DEGENERATE", {"gates": [{"name": "V2", "ok": False}]}),
            ("r_ok", "PROVISIONAL_VALID", {"gates": [{"name": "V2", "ok": True}, {"name": "V1", "ok": True}]})]
    c.executemany("INSERT INTO tehm_rules VALUES (?,?,?)", [(a, b, json.dumps(d)) for a, b, d in rows])
    assert h5_validity_order(c)[0] is True
    # still caught: an admitted rule that skipped V1, or a V2-passing rule with no V1
    c.execute("INSERT INTO tehm_rules VALUES ('r_bad','PROVISIONAL_VALID',?)",
              (json.dumps({"gates": [{"name": "V2", "ok": True}]}),))
    assert h5_validity_order(c)[0] is False


def test_unmeasured_rows_no_longer_block_stale_rule_retirement(tmp_path):
    """Phase F finding 1: an ordinary unmeasured row (rerun failed) made crystallize_all skip
    retirement, so a rule outlived its evidence. Now only corrupt rows block it."""
    for d, (ub, ua) in {"r1": ("12", "8"), "r2": ("11", "7"), "r3": ("10", "6")}.items():
        b0 = TehmMemoryBackend(db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "art")
        rows = [_fix("density_relief", 4, 0, "cleared", SIT, ub, ua),
                {**_fix("density_relief", None, None, "rerun_failed_rc2", SIT), "strategy": "route_relief"}]
        b0.ingest_project(_project(tmp_path, d, rows))
        b0.rebuild()
        b0.close()
    b = TehmMemoryBackend(db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "art")
    conn, _ = b._open()
    live = conn.execute("SELECT count(*) FROM tehm_rule_status WHERE status IN ('candidate','promoted')").fetchone()[0]
    assert live >= 1
    # remove the rule's evidence; rebuild must now retire it despite the unmeasured rows
    conn.execute("UPDATE tehm_dataset_membership SET learner_eligible=0 WHERE transition_id IN "
                 "(SELECT transition_id FROM tehm_transitions WHERE verifier_json LIKE '%\"oracle_complete\": true%' "
                 "OR verifier_json LIKE '%\"oracle_complete\":true%')")
    conn.commit()
    b.rebuild()
    left = conn.execute("SELECT count(*) FROM tehm_rule_status WHERE status IN ('candidate','promoted')").fetchone()[0]
    assert left == 0, left
    b.close()


def test_router_proposes_a_composition_from_component_trials(tmp_path):
    """Phase G2 end to end: component-trial rows (graded severity) -> captured -> the router
    adds a composition after the rule strategies (memory's default since Phase H)."""
    import runtime_router
    from tehm.adapters.r2g_evidence import capture_rows
    flw = {**SIT, "check": "orfs_stage", "violation_class": "place", "error_code": "FLW-0024",
           "util_band": "gt60", "count_band": None}

    def trial(design, edits, before_cfg, sev_after):
        return {"design": design, "platform": "sky130hs", "fix_session_id": "g_" + design, "iter": 1,
                "check": "orfs_stage", "violation_class": "place", "strategy": "component:x",
                "from_stage": "floorplan", "before": 1, "after": 0 if sev_after <= 0 else 1,
                "verdict": "cleared" if sev_after <= 0 else "no_improvement", "config_delta": edits,
                "config_before": before_cfg, "situation": flw, "severity_before": 0.25,
                "severity_after": sev_after, "evidence_tier": "component_trial", "provenance": "component_trial:t"}
    b = TehmMemoryBackend(db_path=tmp_path / "tehm.sqlite", artifact_root=tmp_path / "art")
    conn, store = b._open()
    capture_rows(conn, store, [
        trial("ga", {"CORE_UTILIZATION": "60", "PLACE_DENSITY_LB_ADDON": "0.1"},
              {"CORE_UTILIZATION": "90", "PLACE_DENSITY_LB_ADDON": "0.2"}, -0.12)])
    kw = dict(backend=b, project_dir=tmp_path, check="orfs_stage", design_id="new", platform="sky130hs",
              cfg={"CORE_UTILIZATION": "87"}, reports={}, situation=flw, severity=0.20)
    comp = [s for s in runtime_router.signoff_strategies(**kw) if s["source"] == "tehm_compose"]
    assert comp and comp[0]["config_edits"] == {"CORE_UTILIZATION": "57", "PLACE_DENSITY_LB_ADDON": "0.1"}
    assert comp[0]["memory_trial"] and comp[0]["composition"]["tested"]
    b.close()
