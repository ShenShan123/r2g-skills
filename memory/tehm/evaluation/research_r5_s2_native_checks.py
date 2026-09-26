"""Synthetic adapter checks, not native RTL runs or repair outcomes."""
import unittest
from . import research_r5_s2_native as n

def xml(failed=False):
    failure='<failure message="private expected data"/>' if failed else ''
    return (f'<testsuites><testsuite><property name="random_seed" value="{n.SEED}"/>'
            f'<testcase name="{n.TESTS[0]}" classname="test_axis_skid">{failure}</testcase>'
            f'<testcase name="{n.TESTS[1]}" classname="test_axis_skid"/>'
            '</testsuite></testsuites>').encode()

class Checks(unittest.TestCase):
    def test_actual_format_controls(self):
        for failed,rc,summary in [(False,0,'TESTS=2 PASS=2 FAIL=0 SKIP=0'),(True,2,'TESTS=2 PASS=1 FAIL=1 SKIP=0')]:
            result=n.native_verdict(xml(failed),summary,rc,False)
            self.assertEqual(result['obligations']['target'],'FAIL' if failed else 'PASS')
            self.assertEqual(result['obligations']['preservation'],'PASS')

    def test_nonpassable_missing_or_conflicting_evidence(self):
        good=xml(); summary='TESTS=2 PASS=2 FAIL=0 SKIP=0'
        cases=[(None,summary,0,False),(b'broken',summary,0,False),
            (good,'',0,False),(good,summary+summary,0,False),(good,summary,2,False),
            (good,summary,True,False),(good,summary,0,True),
            (xml(True),summary,2,False),(good.replace(n.SEED.encode(),b'0'),summary,0,False),
            (good.replace(b'classname="test_axis_skid"',b'classname="other"'),summary,0,False),
            (good.replace(n.TESTS[0].encode(),n.TESTS[1].encode()),summary,0,False),
            (good.replace(b'</testcase>',b'<skipped/></testcase>'),summary,0,False),
            (good.replace(b'</testcase>',b'<error/></testcase>'),summary,0,False)]
        for args in cases:
            with self.subTest(args=args), self.assertLogs if False else self.subTest():
                self.assertEqual(set(n.native_verdict(*args)['obligations'].values()),{'UNKNOWN'})

    def test_conservative_system_construct_gate(self):
        self.assertTrue(n.admitted_preprocessed(b'module a; endmodule'))
        for data in [b'',b'x\0y',b'$system("command");',b'// $display',b'$fopen("results.xml");',b'x'*262145]:
            self.assertFalse(n.admitted_preprocessed(data))

if __name__=='__main__': unittest.main(verbosity=2)
