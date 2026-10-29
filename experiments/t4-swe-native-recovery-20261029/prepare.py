"""Prepare a separate native-protocol recovery; never edit a parent campaign."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent / 't4-swe20-local-20261026'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def valid_collector(episode):
    path = episode / 'collector-recovery.json'
    if not path.exists():
        return False
    receipt = load(path)
    if not receipt.get('evaluation_grade'):
        return False
    grade_path = Path(receipt['evaluation_grade'])
    grade = load(grade_path)
    return (receipt['raw_result_sha256'] == sha(episode / 'result.json')
            and receipt.get('additional_model_requests') == 0
            and receipt.get('native_patch_matches_trajectory_and_final_worktree') is True
            and receipt.get('verified_patch_status') == grade.get('status') == 'PASS'
            and grade.get('returncode') == 0
            and grade['patch_sha256'] == receipt['patch_sha256']
            and sha(grade_path.parent / 'test-output.txt') == grade['test_output_sha256']
            and receipt['actual_usage'].get('unknown_requests') == 0
            and receipt['actual_usage'].get('open_requests') == 0)


def main():
    if (ROOT / 'inputs').exists():
        raise ValueError('already prepared; never overwrite a recovery')
    sys.path.insert(0, str(PARENT / 'adapters'))
    from run_swe import check_freeze
    base_registration = check_freeze()
    rows = load(PARENT / 'reports/results.json')['episodes']
    cells, preserved, collector_passes = [], {}, []
    for row in rows:
        task, arm = row['task'], row['arm']
        episode = PARENT / 'hosts/swe-agent' / arm / 'episodes' / task
        for name in ['result.json', 'started.json', 'provider/requests.jsonl',
                     'submitted.patch', 'collector-recovery.json',
                     'read-only-history/tehm.sqlite', 'read-only-history/snapshot.json']:
            path = episode / name
            if path.is_file():
                preserved[str(path)] = sha(path)
        if row['status'] in {'ACCEPTED', 'NOT_ACCEPTED', 'QUALIFICATION_BLOCKED'}:
            continue
        if valid_collector(episode):
            collector_passes.append({'task': task, 'arm': arm,
                                     'receipt': str(episode / 'collector-recovery.json')})
            continue
        if row['status'] not in {'ACCOUNTING_FAILURE', 'TECHNICAL_STOP', 'VERIFICATION_UNKNOWN'}:
            raise ValueError('unreviewed parent status: ' + row['status'])
        cell = {'task': task, 'arm': arm, 'parent_episode': str(episode),
                'parent_status': row['status'], 'attempt': 'native-recovery-1'}
        if arm == 'tehm':
            snapshot = episode / 'read-only-history'
            if not (snapshot / 'tehm.sqlite').is_file():
                raise ValueError('original task-start history is missing: ' + task)
            cell.update(history_snapshot=str(snapshot),
                        history_db_sha256=sha(snapshot / 'tehm.sqlite'))
        cells.append(cell)
    if len(cells) != 15 or len(collector_passes) != 1:
        raise ValueError('reviewed recovery scope changed')
    for tree in ['adapters', 'tests']:
        shutil.copytree(PARENT / tree, ROOT / tree,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (ROOT / 'code').symlink_to(PARENT / 'code', target_is_directory=True)
    (ROOT / 'sources').mkdir()
    (ROOT / 'sources/SWE-bench-v4.1.0').symlink_to(PARENT / 'sources/SWE-bench-v4.1.0',
                                                target_is_directory=True)
    for name in ['env-deps', 'bootstrap', 'python-runtimes']:
        if (PARENT / name).exists():
            (ROOT / name).symlink_to(PARENT / name, target_is_directory=True)
    (ROOT / 'inputs/evaluator-private').mkdir(parents=True)
    for name in ['public-tasks.json', 'native.yaml', 'tehm.yaml']:
        shutil.copy2(PARENT / 'inputs' / name, ROOT / 'inputs' / name)
    shutil.copy2(PARENT / 'inputs/evaluator-private/tasks.json',
                 ROOT / 'inputs/evaluator-private/tasks.json')
    (ROOT / 'inputs/evaluator-private/tasks.json').chmod(0o600)
    for task in dict.fromkeys(c['task'] for c in cells):
        src = PARENT / 'qualification' / task
        q = load(src / 'qualification.json')
        if q['status'] != 'PASS':
            raise ValueError('recovery cannot bypass qualification')
        suffix = '-r3' if q.get('previous_receipt') == 'qualification-before-runtime-pythonpath.json' else ''
        for label in ['baseline', 'reference']:
            q[label + '_grade'] = q.get(label + '_grade', str(src / (label + '-eval' + suffix) / 'grade.json'))
        dst = ROOT / 'qualification' / task
        dst.mkdir(parents=True)
        (dst / 'qualification.json').write_text(json.dumps(q, indent=2) + '\n')
    (ROOT / 'reports').mkdir()
    preserved[str(PARENT / 'reports/results.json')] = sha(PARENT / 'reports/results.json')
    (ROOT / 'inputs/plan.json').write_text(json.dumps({
        'parent': str(PARENT), 'retry_cells': cells, 'collector_passes_not_rerun': collector_passes,
        'preserved_parent_files': preserved,
        'external_files': base_registration['external_files'],
        'native_commit': base_registration['native_commit'],
        'authorization': 'User: 修复后按照旧版的继续跑一下中断的那些',
        'scope': '15 interrupted cells only; six raw passes and one verified collector pass not rerun',
        'native_stopping': base_registration['native_stopping'],
        'online_request_cap': None, 'cumulative_token_cap': None, 'cumulative_money_cap': None,
        'implicit_retries': 0, 'model': 'deepseek-flash',
        'memory': 'each recovery uses its original task-start snapshot; no own/future outcomes imported',
        'unknown_policy': 'stop affected cell, preserve UNKNOWN, skip to remaining cells; auth/payment/identity faults global-stop',
        'qualification_blocked_tasks': 'not included; separate environment-repair workstream',
    }, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'prepared': True, 'retry_cells': len(cells),
                      'collector_passes_not_rerun': collector_passes}))


if __name__ == '__main__':
    main()
