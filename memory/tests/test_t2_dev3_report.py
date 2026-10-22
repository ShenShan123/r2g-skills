"""Whole-trajectory report: UNKNOWN stays UNKNOWN, flows link to actions through the chain tag."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
sys.path.insert(0, str(ROOT / 'memory'))
import t2_dev3_report as R  # noqa: E402


def episode(root, task, arm, rep, flows, accepted=False, invalid=0, usage_ok=True):
    d = root / 'episodes' / f"{task.replace('/', '__')}__{arm}__r{rep}"
    (d / 'reports').mkdir(parents=True)
    (d / 'episode.json').write_text(json.dumps({
        'task': task, 'arm': arm, 'repeat': rep, 'status': 'ACCEPTED' if accepted else 'NOT_ACCEPTED', 'accepted': accepted,
        'wall_s': 600, 'terminal_reason': 'x', 'accounting': {'passed': True}, 'resources': {'spent': {'flows': flows}, 'flows_by_component': {'catalogue': flows}},
        'token_usage': {'llm_requests': 1, 'total_tokens': 100 if usage_ok else None, 'measurement_complete': usage_ok}}))
    rows = [{'iter': i, 'strategy': 'route_relief', 'verdict': 'regression', 'rolled_back': True,
             'config_delta': json.dumps({'CORE_UTILIZATION': '57'}), 'post_run_tag': f'RUN_{i}'} for i in range(invalid)]
    (d / 'reports/fix_log.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    (d / 'reports/resource_ledger.jsonl').write_text(''.join(
        json.dumps({'type': 'flow_run', 'component': 'catalogue', 'run_tag': f'RUN_{i}', 'rc': 0}) + '\n' for i in range(invalid)))
    return d


def test_summary_pairs_each_arm_with_nb_and_counts_invalid_and_repeated(tmp_path):
    episode(tmp_path, 'chacha_core/u72', 'NB', 1, 7, invalid=3)
    episode(tmp_path, 'chacha_core/u72', 'TM0', 1, 4, accepted=True, invalid=0)
    eps = [R.load_episode(d) for d in sorted((tmp_path / 'episodes').glob('*'))]
    nb = next(e for e in eps if e['arm'] == 'NB')
    assert nb['invalid_attempts'] == 3 and nb['repeated_executions'] == 2 and nb['flows'] == 7
    assert nb['trajectory'][0]['action'] == 'route_relief' and nb['trajectory'][0]['source'] == 'catalogue'
    summ = R.summarise(eps)
    row = summ['paired_vs_nb'][0]
    assert (row['arm'], row['flows_delta_vs_nb'], row['accepted'], row['nb_accepted']) == ('TM0', -3, True, False)
    assert 'Paired with NB' in R.to_markdown(eps, summ)


def test_missing_usage_is_unknown_not_zero(tmp_path):
    episode(tmp_path, 'riscv_core/u19', 'NB', 1, 3, usage_ok=False)
    e = R.load_episode(next((tmp_path / 'episodes').glob('*')))
    assert e['tokens'] is None and e['tokens_complete'] is False
    assert R.summarise([e])['by_task_arm'][0]['tokens_mean'] is None


def test_withheld_episodes_stay_in_the_denominator_and_validation_is_reported(tmp_path):
    episode(tmp_path, 'chacha_core/u72', 'NB', 1, 5)
    plan = [{'task': 'chacha_core/u72', 'arm': 'NB', 'repeat': 1}, {'task': 'chacha_core/u72', 'arm': 'TM0', 'repeat': 1},
            {'task': 'riscv_core/u19', 'arm': 'NB', 'repeat': 1}]
    (tmp_path / 'registration.json').write_text(json.dumps({'plan': plan}))
    (tmp_path / 'amendment-1.json').write_text(json.dumps({'A_withheld_episodes': {
        'episodes': [{'task': 'riscv_core/u19', 'arm': 'NB', 'repeat': 1}]}}))
    eps = [R.load_episode(d) for d in sorted((tmp_path / 'episodes').glob('*'))]
    denom = R.denominator(tmp_path, eps)
    assert {(d['task'], d['arm']): d['status'] for d in denom} == {
        ('chacha_core/u72', 'NB'): 'EXECUTED', ('chacha_core/u72', 'TM0'): 'NOT_EXECUTED',
        ('riscv_core/u19', 'NB'): 'WITHHELD_NOT_EXECUTED'}
    md = R.to_markdown(eps, R.summarise(eps), denom)
    assert 'WITHHELD_NOT_EXECUTED' in md and 'Strict validation' in md and 'not observable' in md
    v = eps[0]['validation']
    assert v['failed_checks'] == 'UNKNOWN' and v['envelope'] == 'UNKNOWN'          # nothing recorded -> UNKNOWN


def test_a_budget_or_technical_stop_is_never_a_repair_failure(tmp_path):
    base = {'status': 'NOT_ACCEPTED', 'accounting': {'passed': True}}
    assert R.outcome_class(base, [], {}) == 'MEASURED_NOT_ACCEPTED'
    assert R.outcome_class({**base, 'status': 'ACCEPTED'}, [], {}) == 'ACCEPTED'
    assert R.outcome_class({**base, 'status': 'RUNNING'}, [], {}) == 'INCOMPLETE'
    assert R.outcome_class(base, [], {'refused': 1}) == 'BUDGET_CONSTRAINED'
    assert R.outcome_class(base, [{'type': 'flow_run', 'budget_stop': True}], {}) == 'BUDGET_CONSTRAINED'
    assert R.outcome_class({**base, 'status': 'ACCOUNTING_FAILURE', 'accounting': {'passed': False}}, [], {}) == 'TECHNICALLY_TERMINATED'
    assert R.outcome_class({**base, 'technical_failures': [{'kind': 'x'}]}, [], {}) == 'TECHNICALLY_TERMINATED'
    assert R.outcome_class({**base, 'accounting': {}}, [], {}) == 'TECHNICALLY_TERMINATED'     # unverified accounting
    episode(tmp_path, 'chacha_core/u72', 'NB', 1, 4)
    row = R.summarise([R.load_episode(next((tmp_path / 'episodes').glob('*')))])['by_task_arm'][0]
    assert (row['accepted'], row['measured_not_accepted'], row['stopped_budget_or_technical']) == (0, 1, 0)


def test_invalid_attempts_count_failed_reruns_and_empty_edits_are_never_repeats(tmp_path):
    d = tmp_path / 'episodes' / 'chacha_core__u72__NB__r1'
    (d / 'reports').mkdir(parents=True)
    (d / 'episode.json').write_text(json.dumps({'task': 'chacha_core/u72', 'arm': 'NB', 'repeat': 1, 'status': 'ACCEPTED',
        'accepted': True, 'accounting': {'passed': True}, 'resources': {'spent': {'flows': 5}}, 'token_usage': {}}))
    rows = [{'iter': 1, 'strategy': 'route_relief', 'verdict': 'rerun_failed_still_congested', 'config_delta': '{}'},
            {'iter': 2, 'strategy': 'route_relief', 'verdict': 'cleared', 'config_delta': json.dumps({'CORE_UTILIZATION': '45'})},
            {'iter': 1, 'strategy': 'recipe_x', 'verdict': 'memory_rerun_failed_rc2', 'rolled_back': True, 'config_delta': '{}'},
            {'iter': 2, 'strategy': 'recipe_x', 'verdict': 'memory_rerun_failed_rc124', 'rolled_back': True, 'config_delta': '{}'},
            {'iter': 3, 'strategy': 'drv', 'verdict': 'applied', 'config_delta': json.dumps({'M': '10'})},
            {'iter': 4, 'strategy': 'drv', 'verdict': 'applied', 'config_delta': json.dumps({'M': '10'})}]
    (d / 'reports/fix_log.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    (d / 'reports/resource_ledger.jsonl').write_text('')
    e = R.load_episode(d)
    assert e['invalid_attempts'] == 3            # still-congested, and two failed memory reruns
    assert e['repeated_executions'] == 1         # only the second identical non-empty drv edit
