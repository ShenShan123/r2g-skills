"""Flow scripts journal commands/summaries/bugs into the journal DB (spec §5.2)."""
import json
import os
import subprocess
from pathlib import Path

import journal_db

SKILL = Path(__file__).resolve().parents[1]


def test_run_orfs_stage_journals_action_and_summary(tmp_path):
    """_journal_stage <stage> <status> <elapsed> <log> appends a tool_invoke
    action + a log summary; on failure also a tool_bugs row."""
    db = tmp_path / "journal.sqlite"
    proj = tmp_path / "proj"
    (proj / "backend").mkdir(parents=True)
    log = proj / "backend" / "5_route.log"
    log.write_text("[ERROR DRT-0085] cannot fix\nSignal 11 received\n")
    env = dict(os.environ, R2G_JOURNAL_DB=str(db))
    r = subprocess.run(
        ["bash", "-c",
         f'R2G_SOURCE_ONLY=1 source "{SKILL}/scripts/flow/run_orfs.sh"; '
         f'PROJECT_DIR="{proj}"; PLATFORM=nangate45; '
         f'_journal_stage route fail 42 "{log}"'],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    act = c.execute("SELECT action_type, payload_json FROM actions").fetchone()
    assert act[0] == "tool_invoke"
    assert json.loads(act[1])["stage"] == "route"
    assert c.execute("SELECT COUNT(*) FROM log_summaries").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM tool_bugs").fetchone()[0] == 1


def test_fix_signoff_journals_each_knob_delta(tmp_path):
    """fix_signoff.sh's _journal_knob_deltas splits a config_edits dict into one
    config_knob_delta action per knob (spec: each knob INDIVIDUALLY)."""
    db = tmp_path / "journal.sqlite"
    proj = tmp_path / "proj"
    proj.mkdir()
    env = dict(os.environ, R2G_JOURNAL_DB=str(db))
    edits = json.dumps({"SKIP_ANTENNA_REPAIR": "1",
                        "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"})
    r = subprocess.run(
        ["bash", "-c",
         f'R2G_SOURCE_ONLY=1 source "{SKILL}/scripts/flow/fix_signoff.sh"; '
         f'PROJECT_DIR="{proj}"; FIX_SESSION_ID=abcd1234abcd1234; '
         f"_journal_knob_deltas '{edits}' antenna_diode_repair"],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    rows = c.execute("SELECT action_type, fix_session_id, payload_json "
                     "FROM actions ORDER BY action_id").fetchall()
    assert len(rows) == 2
    assert all(t == "config_knob_delta" for t, _, _ in rows)
    assert all(s == "abcd1234abcd1234" for _, s, _ in rows)
    knobs = {json.loads(p)["knob"] for _, _, p in rows}
    assert knobs == {"SKIP_ANTENNA_REPAIR", "MAX_REPAIR_ANTENNAS_ITER_DRT"}


def _source_call(tmp_path, body, *, db=None):
    """Source fix_signoff.sh helpers (R2G_SOURCE_ONLY) and run a bash snippet."""
    db = db or (tmp_path / "journal.sqlite")
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    env = dict(os.environ, R2G_JOURNAL_DB=str(db))
    r = subprocess.run(
        ["bash", "-c",
         f'R2G_SOURCE_ONLY=1 source "{SKILL}/scripts/flow/fix_signoff.sh"; '
         f'PROJECT_DIR="{proj}"; FIX_SESSION_ID=abcd1234abcd1234; {body}'],
        capture_output=True, text=True, env=env)
    return r, db


def test_compute_symptom_id_matches_ingester(tmp_path):
    """A2: fix_signoff.sh's _compute_symptom_id must produce the SAME 16-hex id the
    ingester derives from a fix_log row (symptom.canonical_signature -> symptom_id),
    incl. the route->orfs_stage remap — otherwise the journal symptom_id and the
    knowledge symptom_id silently diverge and the cross-DB link breaks."""
    import symptom
    cases = [("drc", "M3_ANTENNA", "{}"),
             ("lvs", "net_mismatch", '{"nets_balanced": true}'),
             ("route", "route", "{}")]
    for check, vclass, preds in cases:
        r, _ = _source_call(
            tmp_path, f"_compute_symptom_id {check} {vclass} '{preds}'")
        assert r.returncode == 0, r.stderr
        got = r.stdout.strip()
        c2, v2 = (("orfs_stage", vclass or "route") if check == "route"
                  else (check, vclass))
        want = symptom.symptom_id(
            symptom.canonical_signature(c2, v2, json.loads(preds)))
        assert got == want, f"{check}/{vclass}: {got} != {want}"


def test_fix_session_symptom_linked(tmp_path):
    """A2: _journal_knob_deltas given a symptom_id stamps it on every knob row."""
    sid16 = "00ff00ff00ff00ff"
    r, db = _source_call(
        tmp_path,
        f"_journal_knob_deltas '{json.dumps({'CORE_UTILIZATION': '20'})}' "
        f"density_relief {sid16}")
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    rows = c.execute("SELECT symptom_id, action_type FROM actions").fetchall()
    assert rows and all(s == sid16 and t == "config_knob_delta" for s, t in rows)


def test_stacked_fix_parent_chain(tmp_path):
    """A3: the FIRST _journal_knob_deltas call prints its action_id; a later call
    passed that id stamps parent_action_id on its rows (stacked-fix chain)."""
    db = tmp_path / "journal.sqlite"
    r, _ = _source_call(
        tmp_path,
        'first=$(_journal_knob_deltas \'{"K1":"1"}\' strat sym1 ""); '
        '_journal_knob_deltas \'{"K2":"2"}\' strat sym1 "$first"; '
        'echo "FIRST=$first"', db=db)
    assert r.returncode == 0, r.stderr
    first_id = int(r.stdout.split("FIRST=")[1].strip())
    c = journal_db.connect(db)
    rows = dict(c.execute(
        "SELECT json_extract(payload_json,'$.knob'), parent_action_id "
        "FROM actions ORDER BY action_id").fetchall())
    assert rows["K1"] is None          # iteration 1 has no parent
    assert rows["K2"] == first_id      # iteration 2 chains to iteration 1


def test_stage_status_not_referenced_outside_run_stage():
    """Bug (canary 2026-06-17): `STAGE_STATUS` is `local` to run_stage(), but the
    post-loop synth-timeout HINT referenced it at module scope -> with `set -u` a
    synth-stage timeout (exit 124) crashed run_orfs.sh with 'STAGE_STATUS: unbound
    variable' BEFORE results/status were recorded. Module-scope code must use
    MAKE_STATUS. Guard: every STAGE_STATUS reference lives inside run_stage()."""
    src = (SKILL / "scripts" / "flow" / "run_orfs.sh").read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("run_stage()"))
    # function body ends at the first line that is exactly "}" at column 0
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    for i, line in enumerate(src):
        if "STAGE_STATUS" in line and not (start <= i <= end):
            raise AssertionError(
                f"run_orfs.sh:{i+1} references function-local STAGE_STATUS at module "
                f"scope (use MAKE_STATUS): {line.strip()}")


def test_stage_rerun_journaled(tmp_path):
    """Tier B4: _journal_action stage_rerun (called by fix_one before re-invoking
    run_orfs) lands a symptom-linked stage_rerun action."""
    r, db = _source_call(
        tmp_path,
        '_journal_action stage_rerun \'{"from_stage":"route","strategy":"route_relief"}\' '
        '00ff00ff00ff00ff')
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    row = c.execute("SELECT action_type, symptom_id, "
                    "json_extract(payload_json,'$.from_stage') FROM actions").fetchone()
    assert row == ("stage_rerun", "00ff00ff00ff00ff", "route")
