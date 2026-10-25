"""P1c exit conditions on the neutral export (plan memory/evaluation/r2g_P1c_export_plan_20261009.md):
X1 / T10 batched == one-shot import; X2 / T17 rollback status survives export and re-import; X3 re-import adds
nothing and conflicting content is never skipped; copies merge, conflicts are recorded."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tehm import evidence as ev  # noqa: E402
from tehm import neutral_export as nx  # noqa: E402
from tehm.adapters.r2g_evidence import CaptureConflict  # noqa: E402
from tehm_backend import TehmMemoryBackend  # noqa: E402

SIT = {"v": "sit-v1", "check": "drc", "violation_class": "li.3", "error_code": None, "timeout": False,
       "platform": "sky130hs", "die_mode": "auto", "util_band": "le12", "count_band": "1-5"}


def _row(session, it, ts, *, ub="12", ua="8", before=4, after=0, verdict="cleared", rolled_back=False, **kw):
    r = {"fix_session_id": session, "iter": it, "ts": ts, "check": "drc", "strategy": "density_relief",
         "violation_class": "li.3", "from_stage": "floorplan", "before": before, "after": after, "verdict": verdict,
         "config_delta": {"CORE_UTILIZATION": ua}, "config_before": {"CORE_UTILIZATION": ub},
         "situation": dict(SIT)}
    if rolled_back is not None:
        r["rolled_back"] = rolled_back
    r.update(kw)
    return r


def _src(path, design, rows):
    return {"path": path, "design": design, "platform": "sky130hs", "project": design,
            "rows": list(enumerate(rows, 1))}


def _corpus():
    """Three designs; one two-step session per design (a no-improvement step, then a clear)."""
    out = []
    for d, (ub, ua) in {"d1": ("12", "8"), "d2": ("11", "7"), "d3": ("10", "6")}.items():
        out.append(_src("/raw/%s/fix_log.jsonl" % d, d, [
            _row("s_" + d, 1, "2026-10-01T00:0%s:00" % d[-1], ub=ub, ua=ub, after=4, verdict="no_improvement"),
            _row("s_" + d, 2, "2026-10-01T01:0%s:00" % d[-1], ub=ub, ua=ua)]))
    return out


def _store(tmp_path, name):
    return TehmMemoryBackend(db_path=tmp_path / name / "tehm.sqlite", artifact_root=tmp_path / name / "art")


def _snapshot(b):
    """Everything that decides behaviour: attempts, transitions, rules (executable content), statuses, evidence."""
    conn, _ = b._open()
    tup = lambda q: sorted(tuple(r) for r in conn.execute(q).fetchall())
    rules = tup("SELECT rule_id, before_pattern_json, after_pattern_json, hard_preconditions_json, "
                "validity_status FROM tehm_rules")
    return {
        "attempts": tup("SELECT record_id, transition_id, record_digest FROM tehm_attempts"),
        "transitions": sorted(r[0] for r in conn.execute("SELECT transition_id FROM tehm_transitions")),
        "rules": rules,
        "status": tup("SELECT rule_id, target_scope, status FROM tehm_rule_status"),
        "evidence": {r[0]: ev.rule_evidence(conn, r[0]) for r in rules},
    }


def test_x1_t10_batched_import_equals_one_shot_import(tmp_path):
    units, _ = nx.build_units(_corpus())
    one = _store(tmp_path, "one")
    conn, store = one._open()
    nx.import_units(conn, store, units)
    one.rebuild()
    inc = _store(tmp_path, "inc")
    conn, store = inc._open()
    # split mid-session: every batch holds part of some session; rebuild after each batch (online growth)
    for batch in (units[0:1], units[1:4], units[4:]):
        nx.import_units(conn, store, batch)
        inc.rebuild()
    a, b = _snapshot(one), _snapshot(inc)
    assert a["rules"], "fixture must crystallise at least one rule"
    for k in ("attempts", "transitions", "rules", "status", "evidence"):
        assert a[k] == b[k], k
    one.close()
    inc.close()


def test_x2_t17_rollback_status_survives_export_and_reimport(tmp_path):
    rows = [_row("s", 1, "t1", after=4, verdict="no_improvement", rolled_back=True, rollback_reason="no_improvement"),
            _row("s", 2, "t2", rolled_back=False),
            _row("s", 3, "t3", rolled_back=None)]                     # legacy: no marker
    units, log = nx.build_units([_src("/raw/x/fix_log.jsonl", "x", rows)])
    nx.write_export(units, tmp_path / "exp", dedup_log=log)
    back = nx.read_export(tmp_path / "exp")
    assert [u["rollback"]["rolled_back"] for u in back] == ["true", "false", "UNKNOWN"]
    assert back[0]["rollback"]["reason"] == "no_improvement"
    b = _store(tmp_path, "s")
    conn, store = b._open()
    nx.import_units(conn, store, back)
    got = {json.loads(p)["record_scope"]["iter"]: (json.loads(d).get("rolled_back", "UNKNOWN"),
                                                   json.loads(d).get("rollback_reason"))
           for d, p in conn.execute("SELECT t.observation_delta_json, a.provenance_json FROM tehm_attempts a "
                                    "JOIN tehm_transitions t USING (transition_id)")}
    assert got == {1: ("true", "no_improvement"), 2: ("false", None), 3: ("UNKNOWN", None)}
    b.close()


def test_x3_reimport_adds_nothing_and_a_changed_unit_is_a_conflict(tmp_path):
    units, log = nx.build_units(_corpus())
    b = _store(tmp_path, "s")
    conn, store = b._open()
    assert len(nx.import_units(conn, store, units)) == len(units)
    assert nx.import_units(conn, store, units) == []
    changed = json.loads(json.dumps(units[2]))
    changed["raw_row"]["after"] = 3
    with pytest.raises(CaptureConflict, match="after"):
        nx.import_units(conn, store, [changed])
    b.close()


def test_copies_merge_and_conflicts_are_recorded_never_dropped(tmp_path):
    base = _row("s", 1, "t1")
    relabel = {**base, "strategy": "density_relief_v2", "situation": {**SIT, "v": "sit-v2", "perimeter_band": None}}
    measured = {**base, "after": 2}
    units, log = nx.build_units([_src("/raw/a/fix_log.jsonl", "x", [base]), _src("/raw/b/fix_log.jsonl", "x", [base]),
                                 _src("/raw/c/fix_log.jsonl", "x", [relabel])])
    assert len(units) == 1 and log["copies_merged"] == 2 and log["conflicts_annotation"] == 1
    u = units[0]
    assert u["conflict"]["kind"] == "annotation" and set(u["conflict"]["fields"]) == {"situation", "strategy"}
    assert len(u["raw_variants"]) == 2 and u["source_eligible"]                    # every variant kept
    assert u["raw_row"]["situation"]["v"] == "sit-v2" and u["raw_row"]["situation"]["perimeter_band"] is None
    assert u["annotations"]["situation_provenance"]["perimeter_band"] == "definition"
    assert u["raw_row"]["strategy"] == nx.UNKNOWN          # two catalogue labels, no action-derived label recorded
    units, log = nx.build_units([_src("/raw/a/fix_log.jsonl", "x", [base]), _src("/raw/d/fix_log.jsonl", "x", [measured])])
    assert log["conflicts_measured"] == 1 and not units[0]["source_eligible"]
    assert len(units[0]["conflict"]["variants"]) == 2
    b = _store(tmp_path, "s")
    conn, store = b._open()
    with pytest.raises(ValueError, match="excluded from SOURCE"):
        nx.import_units(conn, store, units)
    b.close()


def test_the_unified_unit_does_not_depend_on_source_paths():
    base = _row("s", 1, "t1")
    v2 = {**base, "situation": {**SIT, "v": "sit-v2", "perimeter_band": None}}
    a = nx.build_units([_src("/raw/a/fix_log.jsonl", "x", [base]), _src("/raw/z/fix_log.jsonl", "x", [v2])])[0][0]
    b = nx.build_units([_src("/raw/z/fix_log.jsonl", "x", [base]), _src("/raw/a/fix_log.jsonl", "x", [v2])])[0][0]
    assert a["raw_row"] == b["raw_row"] and a["content_digest"] == b["content_digest"]


def test_an_llm_relabel_merges_through_the_action_derived_label_only():
    edits = {"CORE_UTILIZATION": "8", "PLACE_DENSITY_LB_ADDON": "0.10"}
    old = _row("s", 1, None, strategy="llm_physical_fix", config_delta=edits, seed="r1:L:evaluation")
    new = {**old, "strategy": "llm_edit:CORE_UTILIZATION,PLACE_DENSITY_LB_ADDON"}
    u = nx.build_units([_src("/raw/a/fix_log.jsonl", "x", [old]), _src("/raw/b/fix_log.jsonl", "x", [new])])[0][0]
    assert u["raw_row"]["strategy"] == "llm_edit:CORE_UTILIZATION,PLACE_DENSITY_LB_ADDON"
    assert u["annotations"]["strategy_provenance"] == "derived_from_identical_action"
    other = {**old, "config_delta": {"CORE_UTILIZATION": "9"}, "strategy": "llm_edit:CORE_UTILIZATION"}
    u = nx.build_units([_src("/raw/a/fix_log.jsonl", "x", [old]), _src("/raw/b/fix_log.jsonl", "x", [other])])[0][0]
    assert not u["source_eligible"]                        # a different action is never merged by its label


class _FakeResolver:
    def __init__(self, ratio):
        self.ratio = ratio

    def pre_action_perimeter(self, row, project):
        return (self.ratio, "fake_qualification_run") if self.ratio else (None, "not_located")


def _ppl(seed=True, band="1.75-2.5", v="sit-v2"):
    sit = {**SIT, "v": v, "check": "orfs_stage", "violation_class": "place", "error_code": "PPL-0024",
           "count_band": None, "util_band": "13-30"}
    if v == "sit-v2":
        sit["perimeter_band"] = band
    r = _row("s", 1, "t1", check="orfs_stage", violation_class="place", before=1, situation=sit)
    if seed:
        r["seed"] = "r1:catalogue:evaluation"
    return r


def test_the_ppl_perimeter_band_is_recomputed_recorded_or_unknown():
    unify = lambda rows, res=None: nx.unified_situation(rows, platform="sky130hs", project="r1__d__u70", resolver=res)
    sit, prov = unify([_ppl(), _ppl(v="sit-v1")], _FakeResolver(2.2))           # raw recompute agrees with v2
    assert sit["perimeter_band"] == "1.75-2.5" and prov["perimeter_band"].startswith("recomputed")
    sit, prov = unify([_ppl()], _FakeResolver(1.1))                              # disagrees -> UNKNOWN
    assert sit["perimeter_band"] == nx.UNKNOWN
    sit, prov = unify([_ppl(), _ppl(v="sit-v1")])                                # a seeder's value, unverified
    assert sit["perimeter_band"] == nx.UNKNOWN
    sit, prov = unify([_ppl(seed=False)])                                        # recorded at action time
    assert sit["perimeter_band"] == "1.75-2.5" and prov["perimeter_band"] == "recorded_at_action_sit_v2"
    sit, prov = unify([_ppl(v="sit-v1", seed=False)])                            # v1 never had it
    assert sit["perimeter_band"] == nx.UNKNOWN


def test_a_crashed_prefix_copy_takes_the_full_session_context_and_non_nested_copies_are_excluded():
    a, b, c = _row("s", 1, "t1", after=4, verdict="no_improvement"), _row("s", 2, "t2"), _row("s", 3, "t3")
    units, log = nx.build_units([_src("/raw/full/fix_log.jsonl", "x", [a, b, c]), _src("/raw/crash/fix_log.jsonl", "x", [a, b])])
    u2 = next(u for u in units if u["identity"]["iter"] == 2)
    assert u2["source_eligible"] and u2["capture_context"]["is_terminal"] is False
    d = _row("s", 4, "t4")
    units, log = nx.build_units([_src("/raw/p/fix_log.jsonl", "x", [a, b, c]), _src("/raw/q/fix_log.jsonl", "x", [a, b, d])])
    assert log["conflicts_context"] == 2                   # rows 1 and 2: their sessions are not nested
    assert all(not u["source_eligible"] for u in units if u["identity"]["iter"] in (1, 2))


def test_units_carry_a_text_view_missing_dialogue_and_raw_recovery_evidence():
    rows = [_row("s", 1, "t1", rolled_back=None, cumulative_config=json.dumps({"CORE_UTILIZATION": "8"}))]
    u = nx.build_units([_src("/raw/x/fix_log.jsonl", "x", rows)])[0][0]
    assert u["context"]["dialogue"] == "missing" and "Canonical outcome" in u["text_view"]
    assert u["rollback"]["rolled_back"] == "UNKNOWN" and u["rollback"]["recovery_applied"] is None
    assert [e["basis"] for e in u["rollback"]["recovery_evidence"]] == ["log_cumulative"]
    assert u["outcome"]["new_checklist_status"] == "UNKNOWN"


def test_count_band_is_never_recomputed_from_the_llm_arms_binary_before_flag():
    unify = lambda rows: nx.unified_situation(rows, platform="sky130hs", project="p")[0]["count_band"]
    live = _row("s", 1, "t1", before=1, llm=True, situation={**SIT, "count_band": "6-20"})
    assert unify([live]) == "6-20"                                     # the recorded pre-action count, not the flag
    seed_later = _row("s", 2, None, before=1, seed="r1:L:evaluation", situation={**SIT, "count_band": None})
    assert unify([seed_later]) == nx.UNKNOWN                            # the seeder omitted the count
    assert unify([_row("s", 1, "t1", before=7)]) == "6-20"              # a real count is recomputed


def test_a_restoration_sets_the_capture_marker_but_keeps_the_recorded_status(tmp_path):
    rows = [_row("s", 1, "t1", rolled_back=None), _row("s", 2, "t2", rolled_back=None)]
    units, _ = nx.build_units([_src("/raw/x/fix_log.jsonl", "x", rows)])
    review = {"unit_id": units[0]["unit_id"], "kind": "fix_signoff_log_after_rollback", "restored": True,
              "restored_status": "false", "final_retained_fix": True, "scope": "s"}
    other = {"unit_id": units[1]["unit_id"], "kind": "fix_signoff_log_after_rollback", "restored": False,
             "restored_status": "UNKNOWN"}
    other["dialogue"] = [{"response": "/p/llm_assist1.try0.response.json", "request": None}]
    assert nx.apply_restorations(units, [review, other]) == {"reviewed": 2, "restored": 1, "dialogue_linked": 1}
    assert units[1]["context"]["dialogue"] == "present" and units[0]["context"]["dialogue"] == "missing"
    u0, u1 = units
    assert u0["rollback"]["rolled_back"] == "UNKNOWN" and u0["raw_row"]["rolled_back"] is False   # as recorded kept
    assert u0["raw_variants"][0].get("rolled_back") is None                                        # raw untouched
    assert "rolled_back" not in u1["raw_row"] and u1["rollback"]["recovery_applied"]["status_for_main"] == "UNKNOWN"
    b = _store(tmp_path, "s")
    conn, store = b._open()
    nx.import_units(conn, store, units)
    marks = sorted(json.loads(d).get("rolled_back", "UNKNOWN") for (d,) in conn.execute(
        "SELECT t.observation_delta_json FROM tehm_attempts a JOIN tehm_transitions t USING (transition_id)"))
    assert marks == ["UNKNOWN", "false"]
    b.close()


def test_the_isolation_check_refuses_held_out_or_exposed_families_and_discloses_dev_overlap():
    units = nx.build_units([_src("/raw/a/fix_log.jsonl", "aes", [_row("s", 1, "t1")]),
                            _src("/raw/b/fix_log.jsonl", "eth_demux", [_row("s", 1, "t1")])])[0]
    split = {"family_of_design": {"aes": "secworks/aes", "eth_demux": "verilog-ethernet"},
             "SOURCE": ["secworks/aes", "verilog-ethernet"], "DEV": ["secworks/aes"], "PROBE": [], "TEST": [],
             "exposed_in_development": ["secworks/aes", "verilog-ethernet"]}
    r = nx.check_isolation(units, split)
    assert r["ok"] and r["disclosed_dev_source_overlap"] == ["secworks/aes"]
    bad = {**split, "TEST": ["verilog-ethernet"]}
    errs = {e[0] for e in nx.check_isolation(units, bad)["errors"]}
    assert errs == {"source_unit_in_held_out_family", "held_out_family_exposed_in_development"}
    assert not nx.check_isolation(units, {**split, "family_of_design": {"aes": "secworks/aes"}})["ok"]


def test_x1_episodes_and_rule_lifecycle_do_not_depend_on_arrival_order(tmp_path):
    # a session whose later step arrives in an EARLIER batch (the chronological-batch case), and a rule
    # retired by an intermediate rebuild that a later rebuild produces again
    units, _ = nx.build_units(_corpus())
    one = _store(tmp_path, "one")
    conn, store = one._open()
    nx.import_units(conn, store, units)
    one.rebuild()
    rev = _store(tmp_path, "rev")
    conn2, store2 = rev._open()
    late_first = sorted(units, key=lambda u: -u["capture_context"]["step_index"])
    for batch in (late_first[:3], late_first[3:]):
        nx.import_units(conn2, store2, batch)
        rev.rebuild()
    a, b = _snapshot(one), _snapshot(rev)
    eps = lambda c: sorted(tuple(r) for r in c.execute(
        "SELECT episode_id, initial_state_id, terminal_state_id, terminal_status FROM tehm_episodes "
        "WHERE episode_id IN (SELECT episode_id FROM tehm_episode_steps GROUP BY episode_id "
        "HAVING count(*) = (SELECT max(n) FROM (SELECT count(*) n FROM tehm_episode_steps GROUP BY episode_id)))"))
    assert eps(conn) == eps(conn2)                                 # the full episodes agree
    active = lambda s: sorted(r for r, sc, st in s["status"] if st in ("candidate", "promoted"))
    assert active(a) == active(b)
    one.close()
    rev.close()


def test_a_rule_retired_by_a_rebuild_is_reinstated_when_produced_again(tmp_path):
    from tehm.lifecycle.rule_status import set_status, get_status
    units, _ = nx.build_units(_corpus())
    b = _store(tmp_path, "s")
    conn, store = b._open()
    nx.import_units(conn, store, units)
    b.rebuild()
    rid, sc, st = next(r for r in _snapshot(b)["status"] if r[2] == "candidate")
    set_status(conn, rule_id=rid, target_scope=sc, status="retired",
               provenance={"authority": "crystallize_all", "reason": "not_rebuilt_in_campaign", "campaign_id": "live"})
    b.rebuild()
    assert get_status(conn, rule_id=rid, target_scope=sc)["status"] == "candidate"
    set_status(conn, rule_id=rid, target_scope=sc, status="retired", provenance={"authority": "ab_verdict"})
    b.rebuild()
    assert get_status(conn, rule_id=rid, target_scope=sc)["status"] == "retired"     # other authority: untouched
    b.close()


def test_episode_family_and_episode_mapping_do_not_depend_on_arrival_order(tmp_path):
    # a session whose steps carry DIFFERENT families, captured in step order vs reverse order
    rows = [_row("s", 1, "t1", after=4, verdict="no_improvement", strategy="density_relief"),
            _row("s", 2, "t2", strategy="cell_pad_relief", config_delta={"CELL_PAD_IN_SITES_DETAIL_PLACEMENT": "2"},
                 config_before={"CELL_PAD_IN_SITES_DETAIL_PLACEMENT": "0"})]
    units, _ = nx.build_units([_src("/raw/x/fix_log.jsonl", "x", rows)])
    full = {}
    for name, order in (("fwd", units), ("rev", units[::-1])):
        b = _store(tmp_path, name)
        conn, store = b._open()
        for u in order:
            nx.import_units(conn, store, [u])
        full[name] = sorted(tuple(r) for r in conn.execute(
            "SELECT e.episode_id, e.mechanism_family FROM tehm_episodes e WHERE (SELECT count(*) FROM tehm_episode_steps s "
            "WHERE s.episode_id=e.episode_id) = 2"))
        from tehm.crystallization.build_rules import _load_transitions
        full[name + "_of"] = sorted(_load_transitions(conn)[2].items())
        b.close()
    assert full["fwd"] == full["rev"] and len(full["fwd"]) == 1
    assert full["fwd_of"] == full["rev_of"]


def test_a_transition_maps_to_its_longest_episode_whatever_the_row_order(tmp_path):
    from tehm.crystallization.build_rules import _load_transitions
    rows = [_row("s", 1, "t1", after=4, verdict="no_improvement"), _row("s", 2, "t2")]
    units, _ = nx.build_units([_src("/raw/x/fix_log.jsonl", "x", rows)])
    b = _store(tmp_path, "s")
    conn, store = b._open()
    nx.import_units(conn, store, units)
    full = conn.execute("SELECT episode_id FROM tehm_episode_steps GROUP BY episode_id ORDER BY count(*) DESC").fetchone()[0]
    t1 = conn.execute("SELECT transition_id FROM tehm_episode_steps WHERE episode_id=? AND step_index=0", (full,)).fetchone()[0]
    # a prefix episode row written LAST (as a different arrival order would leave it)
    conn.execute("INSERT INTO tehm_episode_steps (episode_id, step_index, transition_id, branch_id) "
                 "VALUES ('episode_zz_prefix', 0, ?, 'main')", (t1,))
    assert _load_transitions(conn)[2][t1] == full
    b.close()


def test_rule_identity_does_not_depend_on_capture_order(tmp_path):
    # the same experience captured in two orders crystallizes the same rule ids (hole numbering included)
    units, _ = nx.build_units(_corpus())
    ids = {}
    for name, order in (("fwd", units), ("rev", units[::-1])):
        b = _store(tmp_path, name)
        conn, store = b._open()
        for u in order:
            nx.import_units(conn, store, [u])
        b.rebuild()
        ids[name] = sorted(tuple(r) for r in conn.execute(
            "SELECT rule_id, after_pattern_json FROM tehm_rules"))
        b.close()
    assert ids["fwd"] and ids["fwd"] == ids["rev"]
