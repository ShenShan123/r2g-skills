import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmark_runtime import select_cases, projections


def checkpoint(cells):
    return dict(task_id=str(cells),status='PASS',audit={'status':'PASS'},
                record={'mapped_cells':cells,'split':'unassigned'},
                label_counts={s:{t:{'valid':10} for t in ('wirelength','congestion')}
                              for s in ('cts','route')})


def test_selection_covers_sizes_and_largest_without_scores():
    rows=[checkpoint(c) for c in (100,200,400,500,1000,1900,2000,5000,9000)]
    assert [r['task_id'] for r in select_cases(rows)] == ['200','400','1000','1900','5000','9000']


def test_no_sampling_formal_test_split():
    rows=[checkpoint(c) for c in (100,200,500,1000,2000,5000)]
    rows[0]['record']['split']='test'
    with pytest.raises(ValueError,match='formal split'):
        select_cases(rows)


def test_projection_is_explicit_compute_only_estimate():
    rows=[]
    for stage in ('cts','route'):
        for target in ('wirelength','congestion'):
            for size,cells in (('small',100),('medium',1000),('large',5000)):
                rows += [dict(stage=stage,target=target,size_band=size,mapped_cells=cells,
                              train_seconds_median=cells*.01,inference_seconds_median=cells*.005)]*2
    result=projections(rows,{'pool':[{'mapped_cells':1000}]},100,3)['pool']
    assert result['total_hours']['central'] == pytest.approx(4*(.6*10+.2*5)*100*3/3600)
