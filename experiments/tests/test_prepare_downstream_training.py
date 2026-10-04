import importlib.util
from pathlib import Path
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('prep_downstream', TOOLS / 'prepare_downstream_training.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_group_split_and_prior_exposure():
    rows = [dict(design_id=f'd{i}', family_id=f'f{i//2}') for i in range(40)]
    a = mod.assign_groups(rows, {'d0', 'd3'})
    assert a == mod.assign_groups(list(reversed(rows)), {'d0', 'd3'})[::-1]
    assert all(r['split']=='train' for r in a if r['family_id'] in ('f0', 'f1'))
    assert {r['split'] for r in a} == {'train', 'validation', 'test'}
    for family in {r['family_id'] for r in a}:
        assert len({r['split'] for r in a if r['family_id']==family}) == 1


def test_empty_target_not_eligible_but_low_coverage_is():
    counts = {s: {t: {'valid': 1, 'total': 1000} for t in ('wirelength','congestion')}
              for s in ('cts','route')}
    assert mod.valid_common(counts)
    counts['route']['congestion']['valid'] = 0
    assert not mod.valid_common(counts)


def test_insufficient_groups_rejected():
    with pytest.raises(ValueError, match='Insufficient'):
        mod.assign_groups([dict(design_id='x',family_id='x')], {'x'})


def test_wire_audit_exposes_indirect_and_mismatch(tmp_path):
    import csv
    import json
    root = tmp_path / 'design' / 'generated'
    (root / 'labels').mkdir(parents=True)
    raw = tmp_path / 'route.def'
    raw.write_text('UNITS DISTANCE MICRONS 1000 ;\nNETS 2 ;\n'
                   '- n1 + ROUTED met1 ( 0 0 ) ( 1000 0 ) ;\n'
                   '- n2 + ROUTED met1 ( 0 0 ) ( 2000 0 ) ;\nEND NETS\n')
    (root.parent / 'method_config.json').write_text(json.dumps({'route_def':str(raw)}))
    common = dict(wirelength_valid=1, wirelength_label_unit='um', wirelength_label_transform='none',
                  route_segment_net_count=1, route_direct_net_count=1)
    rows = [dict(common, net_name='n1', wirelength_um=1),
            dict(common, net_name='n2', wirelength_um=3),
            dict(common, net_name='renamed', wirelength_um=4, route_segment_net_count=2)]
    with (root / 'labels/net_wirelength_Cg.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    record=dict(design_id='d',graphs={'route':{'path':str(root/'stages/route/heterograph.pt')}})
    report=mod.wire_audit(record)
    assert report['checked_direct']==2
    assert report['status']=='FAIL'
    assert len(report['errors'])==1
    assert report['lineage_not_independently_verified']==['renamed']
