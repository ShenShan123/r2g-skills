import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_downstream_pilot import bounded_command, select_cases, timing_is_confirmed_empty


def test_selection_is_size_ordered_and_does_not_reuse_existing_graphs():
    rows = []
    for i, cells in enumerate((110, 120, 510, 530, 2040)):
        rows.append(dict(task_id=str(i), mapped_cells=cells, export_candidate=True,
            provisional_family_id='same' if i in (1, 2) else str(i), run_dir='/run',
            result_path='/result', result_sha256='hash', repo='repo', commit='commit',
            top_module='top', normalized_source_sha256='norm', source_closure_sha256='closure',
            coverage_state='unknown', physical_clean_flag=False, timing_label_review_required=True))
    inventory = dict(records=rows, graph_records=[dict(task_id='0', status='recorded_pass')])
    picked = select_cases(inventory)
    assert [r['task_id'] for r in picked] == ['1', '3', '4']
    assert len({r['family_id'] for r in picked}) == 3


def test_bounded_command_records_success_and_timeout(tmp_path):
    success = bounded_command([sys.executable, '-c', 'print("ok")'], tmp_path / 'success.log', 5)
    assert success['returncode'] == 0
    assert 'ok' in (tmp_path / 'success.log').read_text()
    timeout = bounded_command([sys.executable, '-c', 'import time; time.sleep(30)'],
                              tmp_path / 'timeout.log', .1)
    assert timeout['returncode'] == 124
    assert timeout['error'] == 'timeout'


def test_empty_timing_is_not_confused_with_tool_timeout_or_other_errors(tmp_path):
    (tmp_path / 'time_rpt').mkdir()
    (tmp_path / 'logs').mkdir()
    for name in ('paths_max.rpt', 'paths_min.rpt'):
        (tmp_path / 'time_rpt' / name).write_text('No paths found.\n')
    (tmp_path / 'logs/timing.log').write_text('OpenSTA produced no paths (max=0, min=0)')
    state = dict(commands=[dict(returncode=0), dict(returncode=1)])
    assert timing_is_confirmed_empty(state, tmp_path)
    state['commands'][1]['returncode'] = 124
    assert not timing_is_confirmed_empty(state, tmp_path)
    state['commands'][1]['returncode'] = 1
    (tmp_path / 'logs/timing.log').write_text('crashed')
    assert not timing_is_confirmed_empty(state, tmp_path)
