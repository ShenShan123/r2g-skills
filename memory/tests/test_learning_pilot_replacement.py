import copy
import importlib.util
from pathlib import Path
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1] / 'evaluation/tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('replacement_test', TOOLS / 'learning_pilot_replacement.py')
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def roster():
    rows = [{'design_id': str(i), 'group_id': 'g' + str(i), 'design': 'd' + str(i),
             'frontend': 'verilog-native', 'baseline_clock_port': 'clk',
             'role': 'LEARN' if i < 20 else 'PROBE', 'order': i + 1} for i in range(28)]
    rows[6]['design_id'] = R.HELD
    return {'selected': {'LEARN': rows[:20], 'PROBE': rows[20:]}}


def test_fixed_reserve_order_skips_all_used_groups_and_never_uses_probe():
    r = roster()
    r['qualification_reserve_order'] = {'LEARN': {'physical': ['used', 'clock'],
                                                'drc': ['a', 'b'], 'timing': ['c']},
                                        'PROBE': {'physical': ['not-authorized']}}
    inv = {k: {'group_id': g} for k, g in [('used', 'g1'), ('clock', 'gc'),
                                        ('a', 'ga'), ('b', 'gb'), ('c', 'g20')]}
    assert R.reserve_order(r, inv) == [('physical', 'clock'), ('drc', 'a'), ('drc', 'b')]


def test_replacement_preserves_probe_and_other_learning_positions():
    r = roster()
    original = copy.deepcopy(r)
    new = dict(r['selected']['LEARN'][6], design_id='new', group_id='new-g', design='new')
    out = R.replace_row(r, new)
    assert out['selected']['PROBE'] == r['selected']['PROBE']
    assert out['selected']['LEARN'][:6] == r['selected']['LEARN'][:6]
    assert out['selected']['LEARN'][7:] == r['selected']['LEARN'][7:]
    assert out['selected']['LEARN'][6]['order'] == 7
    assert r == original


def test_replacement_cannot_share_probe_lineage():
    r = roster()
    new = dict(r['selected']['LEARN'][6], design_id='new', group_id='g20', design='new')
    with pytest.raises(ValueError, match='lineage'):
        R.replace_row(r, new)
