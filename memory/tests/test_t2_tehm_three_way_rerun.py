"""Operator checks are offline and never instantiate EDA/provider clients."""
import json
from pathlib import Path
import sys

import pytest

sys.path[:0] = [str(Path(__file__).parents[1] / 'scripts'), str(Path(__file__).parents[1])]
import t2_tehm_three_way_rerun as O


@pytest.mark.parametrize('fault', ['failure', 'error', 'skipped', 'too_few'])
def test_bad_test_receipt_cannot_freeze(tmp_path, fault):
    n = 238 if fault == 'too_few' else 239
    rows = ['<testcase name="%s"/>' % i for i in range(n)]
    if fault != 'too_few': rows[0] = '<testcase><' + fault + '/></testcase>'
    f = tmp_path / 'tests.xml'; f.write_text('<testsuite>' + ''.join(rows) + '</testsuite>')
    with pytest.raises(ValueError): O.passing_tests(f)


def test_rebase_preserves_parameters_sdc_and_reference_areas(tmp_path):
    src = tmp_path / 'source'; (src / 'constraints').mkdir(parents=True); (src / 'rtl').mkdir()
    config = 'export CORE_UTILIZATION = 20\nexport SDC_FILE = ' + str(src / 'constraints/constraint.sdc') + '\n'
    (src / 'constraints/config.mk').write_text(config)
    (src / 'constraints/constraint.sdc').write_text('create_clock -period 5 [get_ports clk]\n')
    import t2a_envelope as E
    m = {'source_project': str(src), 'initial_config': E.effective_config(config),
         'immutable': {'files': {'constraints/constraint.sdc': 'fixed-hash'}},
         'area': {'stdcell': {'ref': 100, 'max_rel_increase': .55}}}
    O.save(src / 'manifest.json', m)
    dst = tmp_path / 'destination'; O.rebase_template(src, dst)
    new = O.load(dst / 'manifest.json')
    assert new['initial_config']['CORE_UTILIZATION'] == '20' and new['area'] == m['area']
    assert new['initial_config']['SDC_FILE'] == str(dst / 'constraints/constraint.sdc')
    assert (dst / 'constraints/constraint.sdc').read_bytes() == (src / 'constraints/constraint.sdc').read_bytes()
    assert O.load(src / 'manifest.json') == m


def test_collector_preserves_all_old_cells_and_does_not_inherit_old_tehm_aggregate(tmp_path):
    base = tmp_path / 'old'; base.mkdir(); root = tmp_path / 'new'; root.mkdir()
    cells = [{'design': 'a', 'task': 'task', 'arm': arm, 'status': status}
             for arm, status in [('NB', 'ACCEPTED'), ('TM0', 'NOT_ACCEPTED'), ('AMEM', 'ACCOUNTING_FAILURE')]]
    original = {'episodes': cells, 'arms': {'TM0': {'accepted': 999}}, 'complete': False}
    O.save(base / 'table2-results.json', original)
    O.save(root / 'registration.json', {'base': str(base), 'plan': [
        {'root': str(root / 'folds/a'), 'design': 'a', 'task': 'task', 'arm': 'TM0'}]})
    O.collect(root)
    new = O.load(root / 'table2-results.json')
    assert O.load(base / 'table2-results.json') == original
    assert new['previous_TEHM_episodes'] == [cells[1]]
    assert new['episodes'][:2] == [cells[0], cells[2]]
    assert new['episodes'][2]['status'] == 'NOT_STARTED' and 'arms' not in new
    assert not new['complete']


def test_receipt_audit_does_not_accept_an_unbound_local_trial(tmp_path):
    (tmp_path / 'reports').mkdir()
    (tmp_path / 'reports/resource_ledger.jsonl').write_text(json.dumps({
        'type': 'memory_procedure_consumption', 'flow_launched': True}) + '\n')
    assert 'missing local selection binding' in O.receipt_errors(tmp_path)
    assert 'missing local action journal' in O.receipt_errors(tmp_path)
