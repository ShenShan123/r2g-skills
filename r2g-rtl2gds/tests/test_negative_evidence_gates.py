"""2026-07-04 negative-evidence hygiene + consumption.

The store held 2376 'abandoned' fix_trajectories of which ~1957 never tried a
real strategy (path all 'none'), violation classes carried raw quoted KLayout
text ("'m3.2'", 100-char rule prose) fragmenting the symptom index, and NOTHING
consumed the negative evidence at apply time — the same (design, symptom,
strategy) triple was re-abandoned up to 112 times across sessions. These tests
lock:
  - symptom.normalize_class + canonical_signature normalization;
  - _build_trajectory: 'not_attempted' outcome for none-only episodes, legacy
    quoted-class signatures healed on rebuild;
  - diagnose _annotate_live_gates: dead-here counting from fix_events and the
    lifecycle-status annotation;
  - _live_auto_strategy: skips dead-here and A/B-demoted ('shadow') strategies
    in blind live runs; --rank-first and R2G_FIX_RETRY_DEAD bypass;
  - corrupt heuristics.json degrades to cold start instead of crashing.
"""
import json
from pathlib import Path

import diagnose_signoff_fix as dsf
import knowledge_db
import learn_heuristics
import symptom


# ── normalize_class ──────────────────────────────────────────────────────────

def test_normalize_class_strips_quotes_and_prose():
    assert symptom.normalize_class("'m3.2'") == "m3.2"
    assert symptom.normalize_class('"M4.S.5"') == "M4.S.5"
    assert symptom.normalize_class(
        "'LIG.LISD.S.7 : Min. corner-to-corner spacing : 15nm'") == "LIG.LISD.S.7"
    assert symptom.normalize_class("'  '") is None
    assert symptom.normalize_class(None) is None
    assert symptom.normalize_class("METAL5_ANTENNA") == "METAL5_ANTENNA"


def test_canonical_signature_normalizes_class():
    sig = symptom.canonical_signature("drc", "'m3.2'")
    assert sig["class"] == "m3.2"
    # Quoted and bare spellings hash to ONE symptom bucket.
    assert symptom.symptom_id(sig) == symptom.symptom_id(
        symptom.canonical_signature("drc", "m3.2"))


# ── trajectory outcomes + signature healing ──────────────────────────────────

def _ev(**kw):
    base = {"fix_session_id": "s1", "project_path": "/p/d", "design_name": "d",
            "design_family": "f", "platform": "sky130hd", "check_type": "drc",
            "violation_class": "METAL5_ANTENNA", "iter": 1, "strategy": "none",
            "before_count": 3, "after_count": 3, "verdict": "inconclusive",
            "elapsed_s": 1.0}
    base.update(kw)
    return base


def test_none_only_episode_is_not_attempted():
    t = learn_heuristics._build_trajectory([_ev()])
    assert t["outcome"] == "not_attempted"


def test_tried_and_failed_episode_is_abandoned():
    t = learn_heuristics._build_trajectory(
        [_ev(strategy="antenna_diode_repair", verdict="no_change")])
    assert t["outcome"] == "abandoned"
    assert json.loads(t["failed_strategies_json"]) == ["antenna_diode_repair"]


def test_cleared_episode_is_resolved():
    t = learn_heuristics._build_trajectory(
        [_ev(strategy="antenna_diode_repair", verdict="cleared", after_count=0)])
    assert t["outcome"] == "resolved"


def test_rebuild_heals_quoted_class_signature():
    """Events written before normalize_class stored quoted classes; the Tier-2
    rebuild must re-key them into the normalized symptom bucket."""
    quoted_sig = {"check": "drc", "class": "'m3.2'", "predicates": {}}
    ev = _ev(violation_class="'m3.2'",
             symptom_id="deadbeefdeadbeef",
             signature_json=json.dumps(quoted_sig))
    t = learn_heuristics._build_trajectory([ev])
    healed = json.loads(t["signature_json"])
    assert healed["class"] == "m3.2"
    assert t["violation_class"] == "m3.2"
    assert t["symptom_id"] == symptom.symptom_id(
        symptom.canonical_signature("drc", "m3.2"))


def test_rebuild_keeps_normalized_signature_untouched():
    sig = symptom.canonical_signature("drc", "METAL5_ANTENNA")
    sid = symptom.symptom_id(sig)
    ev = _ev(symptom_id=sid, signature_json=json.dumps(sig, sort_keys=True))
    t = learn_heuristics._build_trajectory([ev])
    assert t["symptom_id"] == sid


# ── dead-here + lifecycle gates at apply time ────────────────────────────────

def _plan(*ids, extra=None):
    return {"strategies": [
        {"id": i, "auto_apply": True, "recheck": "drc",
         **(extra.get(i, {}) if extra else {})} for i in ids]}


def test_live_auto_strategy_skips_dead_here(monkeypatch):
    monkeypatch.delenv("R2G_FIX_RETRY_DEAD", raising=False)
    plan = _plan("a", "b", extra={"a": {"dead_here": 3}})
    assert dsf._live_auto_strategy(plan)["id"] == "b"


def test_live_auto_strategy_retry_dead_env_restores(monkeypatch):
    monkeypatch.setenv("R2G_FIX_RETRY_DEAD", "1")
    plan = _plan("a", "b", extra={"a": {"dead_here": 3}})
    assert dsf._live_auto_strategy(plan)["id"] == "a"


def test_live_auto_strategy_skips_shadow_lifecycle(monkeypatch):
    monkeypatch.delenv("R2G_FIX_RETRY_DEAD", raising=False)
    plan = _plan("a", "b", extra={"a": {"lifecycle_status": "shadow"}})
    assert dsf._live_auto_strategy(plan)["id"] == "b"
    # 'parked' (merely unvalidatable) and 'candidate' stay applicable.
    plan = _plan("a", extra={"a": {"lifecycle_status": "parked"}})
    assert dsf._live_auto_strategy(plan)["id"] == "a"


def test_rank_first_bypasses_all_gates(monkeypatch):
    monkeypatch.delenv("R2G_FIX_RETRY_DEAD", raising=False)
    plan = _plan("a", "b", extra={"a": {"dead_here": 5,
                                        "lifecycle_status": "shadow"}})
    assert dsf._live_auto_strategy(plan, rank_first="a")["id"] == "a"


def test_annotate_live_gates_counts_terminal_failures(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    proj = tmp_path / "design"
    proj.mkdir()
    rows = [
        # 2 terminal failures, zero clears -> dead at the default threshold.
        ("density_relief", "no_change"), ("density_relief", "regression"),
        # failed once but ALSO cleared once -> never dead.
        ("antenna_diode_repair", "no_change"), ("antenna_diode_repair", "cleared"),
        # a single failure stays below the threshold.
        ("drc_route_effort", "no_change"),
    ]
    for i, (strat, verdict) in enumerate(rows):
        conn.execute(
            "INSERT INTO fix_events (fix_session_id, project_path, check_type, "
            "iter, strategy, verdict) VALUES (?,?,?,?,?,?)",
            (f"s{i}", str(proj), "drc", 1, strat, verdict))
    conn.commit()
    conn.close()
    plan = _plan("density_relief", "antenna_diode_repair", "drc_route_effort")
    dsf._annotate_live_gates(plan, proj, check="drc", db_path=db)
    by_id = {s["id"]: s for s in plan["strategies"]}
    assert by_id["density_relief"]["dead_here"] == 2
    assert "dead_here" not in by_id["antenna_diode_repair"]
    assert "dead_here" not in by_id["drc_route_effort"]


def test_annotate_live_gates_lifecycle_status(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy,"
        " status, provenance, updated_at) VALUES "
        "('sX','c/small','sky130hd','density_relief','shadow','ab_corpus:0w2l','t')")
    conn.commit()
    conn.close()
    proj = tmp_path / "design"
    proj.mkdir()
    plan = _plan("density_relief", "antenna_diode_repair")
    dsf._annotate_live_gates(plan, proj, check="drc", sid="sX",
                             design_class="c/small", platform="sky130hd",
                             db_path=db)
    by_id = {s["id"]: s for s in plan["strategies"]}
    assert by_id["density_relief"]["lifecycle_status"] == "shadow"
    # Grandfathered (absent row) == promoted -> no annotation, stays applicable.
    assert "lifecycle_status" not in by_id["antenna_diode_repair"]


def test_annotate_live_gates_survives_missing_db(tmp_path, capsys):
    plan = _plan("a")
    out = dsf._annotate_live_gates(plan, tmp_path / "nope", check="drc",
                                   db_path=tmp_path / "no" / "such.sqlite")
    assert out["strategies"][0]["id"] == "a"     # un-annotated, not crashed


# ── corrupt heuristics.json degrades to cold start ───────────────────────────

def test_corrupt_heuristics_cold_start(tmp_path, capsys):
    hp = tmp_path / "heuristics.json"
    hp.write_text("{ corrupted", encoding="utf-8")
    assert dsf.load_indexed_recipe(check="drc", platform="sky130hd",
                                   design_class="c/small", drc={}, lvs={},
                                   heuristics=hp) == (None, {}, "none")
    assert dsf.load_symptom_recipe(check="drc", platform="sky130hd",
                                   drc={}, lvs={}, heuristics=hp) == (None, {})
    err = capsys.readouterr().err
    assert "WARNING" in err and "cold-start" in err


# ── lesson trigger glob matching (2026-07-04 coverage expansion) ─────────────

def test_lesson_class_trigger_globs(tmp_path):
    import search_failures
    doc = tmp_path / "patterns.md"
    doc.write_text(
        "## Antenna\n\n"
        "<!-- r2g-lesson:\n"
        "id: lesson-ant\n"
        "status: active\n"
        'trigger: {check: drc, class: "*_ANTENNA", platform: nangate45}\n'
        "strategy_ids: [antenna_diode_repair]\n"
        "-->\n\nprose\n", encoding="utf-8")
    hit = search_failures.lessons_for_symptom(
        check="drc", vclass="METAL4_ANTENNA", platform="nangate45",
        patterns_path=doc)
    assert [l["id"] for l in hit] == ["lesson-ant"]
    miss = search_failures.lessons_for_symptom(
        check="drc", vclass="density", platform="nangate45", patterns_path=doc)
    assert miss == []


def test_shipped_lessons_cover_key_symptoms():
    """The curated failure-pattern lessons the diagnosis path can actually see
    (2026-07-04: was 3 of 123 sections; the high-value strategy-mapped modes are
    now annotated). Guards against annotation regressions."""
    import search_failures
    got = {l["id"] for l in search_failures.lessons_for_symptom(
        check="drc", vclass="METAL4_ANTENNA", platform="nangate45")}
    assert "lesson-nangate45-antenna-diode" in got
    got = {l["id"] for l in search_failures.lessons_for_symptom(
        check="route", vclass=None, platform="sky130hd")}
    assert {"lesson-route-timeout-relief", "lesson-sky130-route-dense"} <= got
    got = {l["id"] for l in search_failures.lessons_for_symptom(
        check="timing", vclass=None, platform="nangate45")}
    assert "lesson-setup-timing-tiers" in got
    got = {l["id"] for l in search_failures.lessons_for_symptom(
        check="drc", vclass="V1.S.4", platform="asap7")}
    assert "lesson-asap7-drc-deck-floor" in got
