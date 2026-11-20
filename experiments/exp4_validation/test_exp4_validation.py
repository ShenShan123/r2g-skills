"""Unit tests for the shared Exp-4 match logic and ground-truth geometry (run: python3 -m pytest)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from match import set_compare, value_compare  # noqa: E402
from gt_raw import canon, orient_point, oriented_size, is_const  # noqa: E402


def test_set_compare_multiset():
    r = set_compare(['a', 'b', 'b'], ['a', 'b'])
    assert not r['passed'] and r['matched'] == 2 and math.isclose(r['recall'], 1.0)
    assert set_compare([('g', 'A')], [('g', 'A')])['passed']


def test_value_compare_tolerance_and_nan():
    r = value_compare(['x', 'y'], [1.0, float('nan')], [1.0 + 1e-10, float('nan')], 'congestion')
    assert r['passed']
    assert not value_compare(['x'], [float('nan')], [1.0], 'exact')['passed']
    assert not value_compare(['x'], [1.001], [1.0], 'wl_um')['passed']


def test_def_orientation_matches_openroad():
    # INV_X1 pin A centre (0.1125, 0.6125) in a 0.38 x 1.4 cell; FS = mirror about X axis (OpenROAD MX)
    assert orient_point(0.1125, 0.6125, 0.38, 1.4, 'FS') == (0.1125, 1.4 - 0.6125)
    assert orient_point(0.1125, 0.6125, 0.38, 1.4, 'FN') == (0.38 - 0.1125, 0.6125)
    assert oriented_size(0.38, 1.4, 'W') == (1.4, 0.38)


def test_canon_and_constants():
    assert canon(r'core.reg\[4\]$_DFF_') == 'core.reg[4]$_DFF_'
    assert is_const("1'h0") and is_const("1'b1") and not is_const('_123_')
