import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from congestion_audit import capacities, distribution, raw_proxy


def inputs(tmp_path):
    lef = tmp_path/'tech.lef'
    lef.write_text('''LAYER m1
 TYPE ROUTING ;
 DIRECTION HORIZONTAL ;
 PITCH 2 1 ;
END m1
LAYER m2
 TYPE ROUTING ;
 DIRECTION VERTICAL ;
 PITCH 2 1 ;
END m2
''')
    route = tmp_path/'route.def'
    route.write_text('''UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( -1000 -2000 ) ( 30000 30000 ) ;
COMPONENTS 3 ;
- a cell + PLACED ( -1000 -2000 ) N ;
- b cell + FIXED ( 11000 -2000 ) N ;
- c cell + UNPLACED ;
END COMPONENTS
NETS 1 ;
- n ( a P ) ( b P )
 + ROUTED m1 ( -1000 -2000 ) ( 19000 * )
 NEW m2 ( -1000 -2000 ) ( * 18000 ) ;
END NETS
''')
    return {'route_def': str(route), 'lef': [str(lef)], 'congestion_grid_um': 10}


def test_proxy_analytic_units_grid_split_and_masks(tmp_path):
    cfg = inputs(tmp_path)
    assert capacities(cfg['lef'], 10) == [100, 50]
    coords, util, details = raw_proxy(cfg)
    assert coords == {'a': (0, 0), 'b': (1, 0), 'c': None}
    assert util == {(0, 0): .2, (1, 0): .1, (0, 1): .2}
    assert details['segments'] == 2
    assert distribution([0, 0, .2])['zero_fraction'] == 2/3


def test_unsupported_geometry_and_smoothing_rejected(tmp_path):
    cfg = inputs(tmp_path)
    with pytest.raises(ValueError, match='radius'):
        raw_proxy(dict(cfg, congestion_radius=1))
    path = Path(cfg['route_def'])
    path.write_text(path.read_text().replace('( 19000 * )', '( 19000 1000 )'))
    with pytest.raises(ValueError, match='Nonrectilinear'):
        raw_proxy(cfg)


def test_negative_relative_grid_and_unused_grid_zero(tmp_path):
    cfg = inputs(tmp_path)
    path = Path(cfg['route_def'])
    path.write_text(path.read_text().replace('( 19000 * )', '( -11000 * )'))
    _, util, _ = raw_proxy(cfg)
    assert util[(-1, 0)] == .1
    assert util.get((99, 99), 0.) == 0.


def test_patch_offsets_not_counted_as_routed_segments(tmp_path):
    cfg = inputs(tmp_path)
    path = Path(cfg['route_def'])
    expected = raw_proxy(cfg)
    path.write_text(path.read_text().replace('( 19000 * )', '( 19000 * ) RECT ( -100 -50 0 50 )'))
    assert raw_proxy(cfg) == expected
