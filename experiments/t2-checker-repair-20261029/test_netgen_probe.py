import re
import unittest

from netgen_probe import strict_verdict, mutate


class StrictVerdictTests(unittest.TestCase):
    def test_success_requires_final_and_equivalent(self):
        report='Final result: Circuits match uniquely.\n'
        self.assertEqual(strict_verdict(0,report,'PROBE_EQUIVALENT=1')['status'],'clean')
        self.assertEqual(strict_verdict(0,report,'')['status'],'UNKNOWN')

    def test_properties_not_hidden_by_equivalence_flag(self):
        report='Final result: Top level cell failed pin matching.\nThe following cells had property errors:\n INV_X4'
        self.assertEqual(strict_verdict(0,report,'PROBE_EQUIVALENT=1')['status'],'fail')

    def test_nonzero_exit_never_passes(self):
        self.assertEqual(strict_verdict(1,'Final result: Circuits match uniquely.','PROBE_EQUIVALENT=1')['status'],'UNKNOWN')

    def test_missing_final_is_unknown(self):
        self.assertEqual(strict_verdict(0,'Netlists match uniquely.','PROBE_EQUIVALENT=1')['status'],'UNKNOWN')

    def test_mutation_changes_only_one_device(self):
        text='.SUBCKT top A B\nX$1 A B CELL\n.ENDS\nM$1 A B C D NMOS_VTL L=0.05U W=0.315U\n'
        removed=mutate(text,'remove_instance')
        self.assertNotIn('X$1',removed)
        width=mutate(text,'change_width')
        self.assertIn('W=0.715U',width)
        self.assertEqual(width.replace('W=0.715U','W=0.315U'),text)


if __name__=='__main__':unittest.main()
