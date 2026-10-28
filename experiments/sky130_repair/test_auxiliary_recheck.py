import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from run_auxiliary208 import run_recheck,install_immutable


class FreshSignoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.baseline=self.root/'baseline';self.baseline.mkdir()
        for n in ('6_final.def','6_final.v','6_final.gds','config.mk','constraint.sdc'):(self.baseline/n).write_text(n+' original\n')
        (self.baseline/'result.json').write_text(json.dumps(dict(design='case',status='pass',drc_status='clean',lvs_status='clean')))
        (self.baseline/'6_drc.lyrdb').write_text('<report-database><items/></report-database>')
        (self.baseline/'netgen_lvs.rpt').write_text('Final result: Circuits match uniquely.\n')
        self.case=dict(design='case',category='signoff_evidence_review',run=str(self.baseline))
        self.before={p.name:p.read_bytes() for p in self.baseline.iterdir()}

    def run_check(self,write_lvs):
        def drc(run,*args):
            (run/'6_drc.lyrdb').write_text('<report-database><items/></report-database>')
        def lvs(run,*args):
            if write_lvs:(run/'netgen_lvs.rpt').write_text('Final result: Circuits match uniquely.\n')
        return run_recheck(self.case,self.root/'out',None,SimpleNamespace(one_and_record=drc),SimpleNamespace(one_and_record=lvs,shutil=shutil))

    def test_missing_fresh_lvs_cannot_reuse_old_clean_report(self):
        record=self.run_check(False)
        self.assertEqual(record['status'],'not_clean')
        self.assertEqual(record['attempts'][0]['lvs'],'unverified_missing_report')
        run=Path(record['attempts'][0]['run'])
        self.assertTrue((run/'netgen_lvs.rpt.before_recheck').exists())

    def test_fresh_reports_accept_unchanged_layout_and_preserve_baseline(self):
        record=self.run_check(True)
        self.assertEqual(record['status'],'dual_clean')
        self.assertFalse(record['physical_reexecution'])
        self.assertEqual(self.before,{p.name:p.read_bytes() for p in self.baseline.iterdir()})

    def test_shared_helper_upgrade_is_rejected_without_truncating_live_file(self):
        source=self.root/'source.py';target=self.root/'shared/helper.py'
        source.write_bytes(b'original');install_immutable(source,target)
        source.write_bytes(b'different')
        with self.assertRaisesRegex(ValueError,'drain slots'):install_immutable(source,target)
        self.assertEqual(target.read_bytes(),b'original')

    def test_concurrent_slots_install_identical_helper_atomically(self):
        from concurrent.futures import ThreadPoolExecutor
        source=self.root/'source.py';target=self.root/'shared/helper.py';source.write_bytes(b'original'*20000)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:install_immutable(source,target),range(4)))
        self.assertEqual(target.read_bytes(),source.read_bytes())
        self.assertEqual(list(target.parent.iterdir()),[target])


if __name__=='__main__':unittest.main()
