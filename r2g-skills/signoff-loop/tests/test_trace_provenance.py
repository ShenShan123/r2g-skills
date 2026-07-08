"""Cross-DB provenance (spec §5.9, decision 11): both query directions."""
import json

import journal_db
import knowledge_db
import recipe_lifecycle
import trace_provenance

KEY = dict(symptom_id="deadbeef00000001", design_class="crypto/small",
           platform="nangate45", strategy="antenna_diode_repair")


def _setup(tmp_path):
    kc = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(kc)
    jc = journal_db.connect(tmp_path / "journal.sqlite")
    journal_db.ensure_schema(jc)
    # knowledge side: run + trajectory + promoted recipe + trial
    kc.execute("INSERT OR REPLACE INTO runs (run_id, project_path, design_name,"
               " platform, ingested_at, design_class) "
               "VALUES ('r1','/p/d1','d1','nangate45','t','crypto/small')")
    kc.execute("INSERT OR REPLACE INTO fix_trajectories (fix_session_id,"
               " project_path, design_name, platform, check_type,"
               " violation_class, path_json, outcome, winning_strategy,"
               " symptom_id) VALUES ('sess1','/p/d1','d1','nangate45','drc',"
               "'antenna','[]','resolved','antenna_diode_repair',"
               "'deadbeef00000001')")
    kc.execute("INSERT INTO ab_trials (symptom_id, design_class, platform,"
               " strategy, verdict, ts) VALUES (?,?,?,?,'win','t')",
               tuple(KEY.values()))
    recipe_lifecycle.promote(kc, evidence="ab_trial:1", **KEY)
    kc.commit()
    # journal side: action + bug for the same session/run
    journal_db.append_action(jc, project_path="/p/d1", actor="loop",
                             action_type="config_knob_delta",
                             payload={"knob": "SKIP_ANTENNA_REPAIR", "new": "1"},
                             fix_session_id="sess1", run_id="r1")
    journal_db.append_tool_bug(jc, project_path="/p/d1", stage="route",
                               tool="openroad", signature="antenna ratio",
                               symptom_id="deadbeef00000001", run_id="r1")
    return kc, jc


def test_solution_to_origin_tree(tmp_path):
    _setup(tmp_path)
    tree = trace_provenance.solution_origin(
        knowledge_db_path=tmp_path / "knowledge.sqlite",
        journal_db_path=tmp_path / "journal.sqlite", **KEY)
    assert tree["status"] == "promoted"
    assert tree["ab_trials"][0]["verdict"] == "win"
    assert tree["episodes"][0]["design_name"] == "d1"
    assert tree["episodes"][0]["actions"][0]["action_type"] == "config_knob_delta"
    assert tree["bugs"][0]["signature"] == "antenna ratio"


def test_bug_to_solutions(tmp_path):
    _setup(tmp_path)
    sols = trace_provenance.bug_solutions(
        knowledge_db_path=tmp_path / "knowledge.sqlite",
        symptom_id="deadbeef00000001")
    assert sols[0]["strategy"] == "antenna_diode_repair"
    assert sols[0]["status"] == "promoted"
    assert "d1" in sols[0]["proven_on"]


def test_read_only_no_writes(tmp_path):
    kc, jc = _setup(tmp_path)
    before = (tmp_path / "knowledge.sqlite").stat().st_mtime_ns
    trace_provenance.solution_origin(
        knowledge_db_path=tmp_path / "knowledge.sqlite",
        journal_db_path=tmp_path / "journal.sqlite", **KEY)
    assert (tmp_path / "knowledge.sqlite").stat().st_mtime_ns == before
