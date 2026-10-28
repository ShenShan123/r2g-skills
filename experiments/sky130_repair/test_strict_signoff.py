import unittest
from strict_signoff import final_lvs


class FinalVerdictTests(unittest.TestCase):
    def test_subcell_match_never_overrides_top_failure(self):
        prefix='Netlists match uniquely.\nCell pin lists are equivalent.\n'
        self.assertEqual(final_lvs(prefix+'Final result: Top level cell failed pin matching.\n'), 'pin_mismatch')
        self.assertEqual(final_lvs(prefix+'Final result: Netlists do not match.\n'), 'mismatch')

    def test_absence_and_warnings_are_not_pass(self):
        self.assertNotEqual(final_lvs('Netlists match uniquely.\n'), 'clean')
        self.assertNotEqual(final_lvs('Final result: Circuits match with warnings.\n'), 'clean')

    def test_final_unique_match(self):
        self.assertEqual(final_lvs('subcell mismatch\nFinal result: Circuits match uniquely.\n'), 'clean')


if __name__ == '__main__':
    unittest.main()
