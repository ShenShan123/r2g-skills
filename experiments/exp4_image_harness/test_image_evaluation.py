"""Small numerical controls for the reference and failure-sensitive image score."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from image_evaluate import RasterGrid, build_reference, smooth_reflect
from image_scoring import FEATURES, LABELS, PRIMARY_CHANNELS, score_design


TECH = """VERSION 5.8 ;
LAYER H
 TYPE ROUTING ;
 DIRECTION HORIZONTAL ;
 PITCH 3 1 ;
END H
LAYER V
 TYPE ROUTING ;
 DIRECTION VERTICAL ;
 PITCH 2 7 ;
END V
"""
CELLS = """MACRO CELL
 CLASS CORE ;
 SIZE 2 BY 1 ;
 PIN A
  PORT
   LAYER H ;
   RECT 0 0 2 1 ;
  END
 END A
END CELL
MACRO MAC
 CLASS BLOCK ;
 SIZE 2 BY 2 ;
END MAC
"""
HEADER = """VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 1000 2000 ) ( 6000 7000 ) ;
GCELLGRID X 1000 DO 4 STEP 2000 ;
GCELLGRID Y 2000 DO 4 STEP 2000 ;
COMPONENTS 2 ;
- c CELL + PLACED ( 1000 2000 ) N ;
- m MAC + FIXED ( 5000 6000 ) N ;
END COMPONENTS
PINS 2 ;
- p + NET n + DIRECTION OUTPUT + FIXED ( 3000 4000 ) N ;
- alone + NET one + FIXED ( 5000 4000 ) N ;
END PINS
"""
PLACE = HEADER + """NETS 3 ;
- n ( c A ) ( PIN p ) + USE SIGNAL ;
- one ( PIN alone ) + USE SIGNAL ;
- power ( c A ) ( PIN p ) + USE POWER ;
END NETS
END DESIGN
"""
FINAL = HEADER + """NETS 1 ;
- n ( c A ) ( PIN p ) + USE CLOCK
 + ROUTED H ( 1000 3000 ) ( 6000 * ) VIA1 RECT ( -5 -5 5 5 )
 NEW V ( 3000 2000 ) ( * 7000 ) ;
END NETS
SPECIALNETS 1 ;
- power + ROUTED H ( 1000 3000 ) ( 6000 * ) ;
END SPECIALNETS
END DESIGN
"""


class ImageReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "3_place.def").write_text(PLACE)
        (self.root / "6_final.def").write_text(FINAL)
        (self.root / "NangateOpenCellLibrary.tech.lef").write_text(TECH)
        (self.root / "NangateOpenCellLibrary.macro.mod.lef").write_text(CELLS)

    def reference(self):
        return build_reference(self.root, self.root, "nangate45", "tiny")

    def test_exact_gcell_boundary_and_nonzero_origin(self):
        grid = RasterGrid(2000, (0, 0, 60000, 60000), 5700, 5700)
        self.assertEqual(grid.point(14.25, 14.25), (5, 5))
        self.assertEqual(grid.point(14.25 - 1e-6, 14.25), (4, 5))
        shifted = RasterGrid(1000, (1000, 2000, 6000, 7000), 2000, 2000)
        self.assertEqual(shifted.point(3, 4), (1, 1))
        self.assertEqual(shifted.point(6, 7), (2, 2))
        self.assertIsNone(shifted.point(6.01, 7))
        np.testing.assert_array_equal(shifted.area, [[4, 4, 2], [4, 4, 2], [2, 2, 1]])

    def test_hand_computed_features_and_route_labels(self):
        arrays = self.reference()["arrays"]
        density = np.zeros((3, 3)); density[0, 0] = 0.5
        macro = np.zeros((3, 3)); macro[2, 2] = 1
        pins = np.zeros((3, 3)); pins[0, 0] = pins[1, 1] = 1
        np.testing.assert_array_equal(arrays["cell_density"], density)
        np.testing.assert_array_equal(arrays["macro_region"], macro)
        np.testing.assert_array_equal(arrays["pin_density"], pins)
        np.testing.assert_array_equal(arrays["pin_rudy"], pins)
        grid = RasterGrid.from_def(self.root / "6_final.def")
        self.assertAlmostEqual(float(np.sum(arrays["rudy"] * grid.area)), 4.0)
        raw = np.array([[0.5, 0, 0], [1, 1, 0.5], [0.25, 0, 0]])
        np.testing.assert_array_equal(arrays["congestion_raw"], raw)

    def test_feature_input_rejects_post_route_data(self):
        (self.root / "3_place.def").write_text(FINAL)
        with self.assertRaisesRegex(ValueError, "contains routed"):
            self.reference()

    def test_polygon_pin_centre_and_flipped_orientation(self):
        polygon_cells = CELLS.replace("SIZE 2 BY 1", "SIZE 3 BY 4").replace(
            "RECT 0 0 2 1 ;", "POLYGON 0 0 2 0 2 1 1 1 1 3 0 3 ;")
        (self.root / "NangateOpenCellLibrary.macro.mod.lef").write_text(polygon_cells)
        (self.root / "3_place.def").write_text(PLACE.replace("( 1000 2000 ) N", "( 1000 2000 ) FS"))
        # L-shape slabs have centres (1,.5),(.5,2), mean (.75,1.25).
        # FS places this pin at (1+.75, 2+4-1.25) = (1.75,4.75).
        expected = np.zeros((3, 3)); expected[0, 1] = expected[1, 1] = 1
        np.testing.assert_array_equal(self.reference()["arrays"]["pin_density"], expected)

    def test_gaussian_matches_scipy_for_small_and_rectangular_grids(self):
        try:
            from scipy.ndimage import gaussian_filter
        except ImportError:
            self.skipTest("SciPy cross-check available only in the test environment")
        for shape in ((1, 1), (1, 3), (2, 5), (9, 12)):
            array = np.arange(np.prod(shape), dtype=float).reshape(shape) / 7
            np.testing.assert_allclose(smooth_reflect(array), gaussian_filter(array, sigma=1.0), atol=1e-14)


class ImageScoringTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.arrays = {name: np.array([[0, 1], [2, 3]], dtype=np.float32) for name in FEATURES + LABELS}
        self.truth = {"design": "tiny", "platform": "gf180", "grid": {"shape": [2, 2]},
                      "arrays": {name: value.astype(np.float64) for name, value in self.arrays.items()}}

    def score(self, run=None, omit=()):
        for filename, names in (("features.npz", FEATURES), ("labels.npz", LABELS)):
            np.savez(self.root / filename, **{name: self.arrays[name] for name in names if name not in omit})
        return score_design(self.truth, self.root, run or {"exit_code": 0})

    def test_clean_pass_and_smoothing_not_double_counted(self):
        self.assertTrue(self.score()["strict_full_pass"])
        self.arrays["congestion"][0, 0] = 1
        score = self.score()
        self.assertFalse(score["strict_full_pass"])
        self.assertEqual(score["raster_accuracy"], 1)
        self.assertEqual(score["label_accuracy"], 0.875)
        self.assertEqual(tuple(score["primary_channels"]), PRIMARY_CHANNELS)

    def test_single_pin_pixel_error_has_fixed_denominator(self):
        self.arrays["pin_density"][0, 0] = 1
        score = self.score()
        self.assertFalse(score["strict_full_pass"])
        self.assertEqual(score["channels"]["pin_density"]["matched"], 3)
        self.assertAlmostEqual(score["raster_accuracy"], 1 - 1 / 24)

    def test_zero_macro_channel_does_not_raise_accuracy(self):
        self.arrays["macro_region"][:] = 0
        self.truth["arrays"]["macro_region"][:] = 0
        self.arrays["pin_density"][0, 0] = 1
        score = self.score()
        self.assertNotIn("macro_region", score["applicable_primary_channels"])
        self.assertAlmostEqual(score["raster_accuracy"], 0.95)
        self.arrays["pin_density"][0, 0] = 0
        self.arrays["macro_region"][0, 0] = 1
        score = self.score()
        self.assertEqual(score["raster_accuracy"], 1)
        self.assertFalse(score["strict_full_pass"])

    def test_missing_channel_transpose_nan_and_failed_run(self):
        missing = self.score(omit=("cell_density",))
        self.assertEqual(missing["channels"]["cell_density"]["rate"], 0)
        self.assertAlmostEqual(missing["raster_accuracy"], 5 / 6)
        self.arrays["rudy"] = self.arrays["rudy"].T
        self.arrays["pin_rudy"][0, 0] = np.nan
        self.assertFalse(self.score()["strict_full_pass"])
        failure = self.score({"exit_code": 124, "stop_reason": "timeout"})
        self.assertEqual(failure["raster_accuracy"], 0)

    def test_tolerance_is_per_pixel_not_global_maximum(self):
        self.arrays["rudy"] = np.array([[1e-3, 1e6], [0, 0]], dtype=np.float32)
        self.truth["arrays"]["rudy"] = np.array([[0, 1e6], [0, 0]], dtype=float)
        score = self.score()
        self.assertEqual(score["channels"]["rudy"]["matched"], 3)
        self.assertFalse(score["strict_full_pass"])


if __name__ == "__main__":
    unittest.main()
