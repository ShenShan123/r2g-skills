import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnose_downstream_coverage import components, gate_transition, net_summary


def test_buffer_deletion_not_renaming_or_zero_label():
    rows = [dict(inst_name='buf', master='buf_4', congestion_valid='0'),
            dict(inst_name='ff', master='ff_1', congestion_valid='1')]
    floor = {'buf': {'master': 'buf_4', 'placed': False},
             'ff': {'master': 'ff_1', 'placed': False}}
    final = {'ff': {'master': 'ff_2', 'placed': True}}
    result = gate_transition(rows, floor, final, final)
    assert result['removed_between_floorplan_and_place'] == ['buf']
    assert result['removed_masters'] == {'buf_4': 1}
    assert result['valid_final_targets'] == 1


def test_invalid_masks_fail_closed():
    with pytest.raises(ValueError, match='mask mismatch'):
        gate_transition([dict(inst_name='missing', master='buf', congestion_valid='1')], {}, {}, {})
    with pytest.raises(ValueError, match='mask mismatch'):
        gate_transition([dict(inst_name='unplaced', master='buf', congestion_valid='1')], {}, {},
                        {'unplaced': {'master': 'buf', 'placed': False}})


def test_net_reasons_distinguish_ambiguity_and_absence():
    rows = [dict(net_name=str(i), wirelength_valid=v, route_segment_net_count=n,
                 route_lineage_valid=l) for i, (v, n, l) in enumerate(
                     [('1', '1', '1'), ('0', '1', '0'), ('0', '0', '0'), ('0', '1', '1')])]
    assert net_summary(rows)['counts'] == dict(valid=1, nonunique_lineage=1,
        no_stage_net_assignment=1, missing_routed_length=1)


def test_def_names_and_duplicate_canonical_keys(tmp_path):
    path = tmp_path / 'x.def'
    path.write_text('COMPONENTS 1 ;\n- a/b\\[0\\] ff + PLACED ( 0 0 ) N ;\nEND COMPONENTS\n')
    assert components(path) == {'a.b[0]': {'master': 'ff', 'placed': True}}
    path.write_text('COMPONENTS 2 ;\n- a/b ff ;\n- a.b ff ;\nEND COMPONENTS\n')
    with pytest.raises(ValueError, match='Duplicate'):
        components(path)
