import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stage_train import fit_constants, paired_hpwl
from stage_train import evaluate
from stage_data import Preprocessor
from test_stage_pipeline import graph, model


def test_hpwl_is_paired_and_keeps_valid_zero():
    p, y, h = paired_hpwl([8, 9, 10, 11], [0, 4, 5, 6], [0, float('nan'), -1, 7])
    assert p.tolist() == [8, 11]
    assert y.tolist() == [0, 6]
    assert h.tolist() == [0, 7]


def test_hpwl_rejects_mismatched_or_nonfinite_supervision():
    with pytest.raises(ValueError):
        paired_hpwl([1], [1, 2], [3])
    with pytest.raises(ValueError):
        paired_hpwl([np.nan], [1], [3])


def test_constants_train_only_and_grid_deduplication():
    d = SimpleNamespace(y_raw=torch.tensor([0., 0., 3., float('nan')]),
                        valid_mask=torch.tensor([True, True, True, False]),
                        target_grid=torch.tensor([[1, 2], [1, 2], [2, 3], [-1, -1]]))
    records = [{'design_id': 'a', 'split': 'train'}]
    c = fit_constants(records, {'a': d})
    assert c['gate'] == dict(n=3, mean=1., median=0.)
    assert c['occupied_grid'] == dict(n=2, mean=1.5, median=1.5)
    for split in ('test', 'validation'):
        with pytest.raises(ValueError, match='training'):
            fit_constants([dict(records[0], split=split)], {'a': d})


def test_evaluation_constants_use_same_valid_targets():
    g = graph()
    processor = Preprocessor.fit([g], 'congestion')
    d = processor.transform(g)
    constants = fit_constants([{'design_id': 'a', 'split': 'train'}], {'a': d})
    args = SimpleNamespace(target='congestion', pilot=False, neighbors=2, layers=2, batch_size=2)
    result = evaluate(model(processor), [{'design_id': 'b', 'split': 'validation'}],
                      {'b': d}, processor, args, torch.device('cpu'), constants=constants)
    for name in ('mean', 'median'):
        assert result['train_only_constants']['gate'][name]['pooled']['n'] == result['pooled']['n'] == 2
        assert result['train_only_constants']['occupied_grid'][name]['pooled']['n'] == 1
        assert result['train_only_constants']['gate'][name]['pooled']['mae'] == 0
