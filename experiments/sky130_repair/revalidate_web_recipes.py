"""Revalidate this operator's local physical A/B evidence in the web Agent store.

Merge intentionally imports lifecycle pointers as shadow. Rehash the durable
local evidence, replay the ordinary judge, and reuse existing trial UUIDs.
Never copy a promotion pointer, invent a trial, or count a new physical rescue.
Default is a rehearsal against a database backup; --apply requires that rehearsal.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO/'r2g-skills/signoff-loop/knowledge')]
from strict_signoff import inspect_run
from area_trial_evidence import classify
from judge_recipe_ab import validate_arm_scope
import ab_runner
import knowledge_db
import recipe_lifecycle


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def evidence():
    result = []
    for name in ('sky130_recipe_validation_20261026',
                 'sky130_pdn_recipe_validation_20261026',
                 'sky130_area_recipe_validation_20261027'):
        root = REPO/'design_cases'/name
        manifest = root/'durable_evidence.json'
        d = json.loads(manifest.read_text())
        for rel, digest in d['files'].items():
            p = (root/rel).resolve()
            if not p.is_relative_to(root.resolve()) or sha(p) != digest:
                raise ValueError('Durable evidence changed: '+rel)
        if len(d['arms']) != 8 or len({a['source_repository'] for a in d['arms']}) != 2:
            raise ValueError('Two independent families with repeated A/B required')
        for arm in d['arms']:
            run = Path(arm['project'])/'physical_run'
            if d['key']['strategy'] == 'fixed_area_route_relief':
                actual = classify(run, arm['flow_returncode'])
                if actual != arm['acceptance'] or actual['classification'] == 'inconclusive':
                    raise ValueError('Area physical evidence changed')
                arm['verified_success'] = actual['is_success']
            else:
                actual = inspect_run(run)
                if actual != arm['strict_signoff']:
                    raise ValueError('Strict DRC/LVS evidence changed')
                arm['verified_success'] = actual['dual_clean']
        published = json.loads((root/'published.json').read_text())
        if published['key'] != d['key'] or published['evidence_manifest_sha256'] != sha(manifest):
            raise ValueError('Published evidence binding changed')
        result.append((root, d, published))
    return result


def states(conn):
    return {tuple(r[:4]): tuple(r[4:]) for r in conn.execute(
        'SELECT symptom_id,design_class,platform,strategy,status,provenance,status_version FROM recipe_status')}


def revalidate(conn, material):
    before = states(conn)
    counts = tuple(conn.execute('SELECT (SELECT COUNT(*) FROM runs),(SELECT COUNT(*) FROM ab_trials)').fetchone())
    reports = []
    for root, d, published in material:
        key = d['key']; groups = {}
        by_project = {s['project']:s for s in published['runs']}
        for arm in d['arms']:
            sample = by_project[arm['project']]
            cur = conn.execute('SELECT * FROM runs WHERE run_id=?', (sample['run_id'],))
            values = cur.fetchone()
            if values is None:
                raise ValueError('Physical arm missing in local store')
            row = dict(zip([c[0] for c in cur.description], values))
            validate_arm_scope(d, arm, row)
            if (knowledge_db.is_success(row) != arm['verified_success']
                    or sample['is_success'] != arm['verified_success']
                    or sample['wall_s'] != arm['wall_seconds']):
                raise ValueError('Local learning metrics disagree with physical evidence')
            groups.setdefault(arm['subject'], {}).setdefault(arm['role'], []).append(sample)
        trials = []
        for subject, arms in groups.items():
            for role in ('A', 'B'):
                arms[role].sort(key=lambda s:s['repeat'])
                if [s['repeat'] for s in arms[role]] != [0, 1]:
                    raise ValueError('Missing repeated arm')
            verdict, reason = ab_runner.judge_repeated_ex(arms['A'], arms['B'])
            if verdict != 'win':
                raise ValueError('Revalidated A/B is not a win')
            ids = [s['run_id'] for role in ('A', 'B') for s in arms[role]]
            uuid = hashlib.sha256('|'.join(ids).encode()).hexdigest()
            existing = conn.execute('SELECT verdict,arm_a_run_id,arm_b_run_id FROM ab_trials WHERE trial_uuid=?', (uuid,)).fetchone()
            if tuple(existing or ()) != (verdict, arms['A'][0]['run_id'], arms['B'][0]['run_id']):
                raise ValueError('Imported trial does not bind to this verified physical evidence')
            tid = ab_runner.record_trial(conn, key=key, verdict=verdict,
                arm_a_run_id=arms['A'][0]['run_id'], arm_b_run_id=arms['B'][0]['run_id'],
                metrics=dict(arm_a_samples=arms['A'], arm_b_samples=arms['B'], reason=reason),
                match_level=d['planner']['match_level'], trial_uuid=uuid)
            trials.append(dict(subject=subject, trial_id=tid, verdict=verdict))
        status = recipe_lifecycle.get_status(conn, **key)
        if status != 'promoted':
            raise ValueError('Ordinary lifecycle did not promote')
        reports.append(dict(key=key, status=status, trials=trials,
            evidence_manifest=str(root/'durable_evidence.json'),
            evidence_manifest_sha256=sha(root/'durable_evidence.json')))
    after = states(conn)
    allowed = {tuple(d['key'][k] for k in ('symptom_id','design_class','platform','strategy')) for _,d,_ in material}
    if any(before.get(k) != after.get(k) for k in set(before)|set(after) if k not in allowed):
        raise ValueError('Unrelated lifecycle changed')
    if counts != tuple(conn.execute('SELECT (SELECT COUNT(*) FROM runs),(SELECT COUNT(*) FROM ab_trials)').fetchone()):
        raise ValueError('Evidence was duplicated')
    return dict(recipes=reports, physical_experiments_started=0, runs_and_trials_added=0,
                unrelated_lifecycle_unchanged=True, campaign_yield_changed=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args(); a.db = a.db.resolve(); a.out.mkdir(parents=True, exist_ok=True)
    if a.db == knowledge_db.DEFAULT_DB_PATH.resolve() or not a.db.is_file():
        raise ValueError('An existing private Agent store is required')
    os.environ['R2G_JOURNAL'] = '0'
    material = evidence()  # Verify ALL physical evidence before any live write.
    if a.apply:
        rehearsal = json.loads((a.out/'rehearsal.json').read_text())
        if rehearsal['source_db'] != str(a.db):
            raise ValueError('Rehearsal belongs to another store')
        if [r['evidence_manifest_sha256'] for r in rehearsal['recipes']] != [sha(r/'durable_evidence.json') for r,_,_ in material]:
            raise ValueError('Evidence changed since rehearsal')
    backup = a.out/('before_apply.sqlite' if a.apply else 'rehearsal.sqlite')
    if backup.exists():
        raise ValueError('Use a new evidence directory; existing backup preserved')
    with sqlite3.connect('file:'+str(a.db)+'?mode=ro', uri=True) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    target = a.db if a.apply else backup
    with knowledge_db.connect(target) as conn:
        report = revalidate(conn, material)
    report.update(source_db=str(a.db), target_db=str(target), applied=a.apply)
    (a.out/('applied.json' if a.apply else 'rehearsal.json')).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
