import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_campaign as campaign


class ReviewedPhyCoreFloorTests(unittest.TestCase):
    def test_exact_pdn_failure_reuses_baseline_floor_without_changing_sdc(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'baseline';base.mkdir()
            for name,text in [('config.mk','export DESIGN_NAME = itrx_aib_phy_io_buf_rx\nexport CORE_UTILIZATION = 50\n'),('constraint.sdc','original clock and IO constraints\n'),('result.json','{}')]:
                (base/name).write_text(text)
            design='C03954__itrx_aib_phy_io_buf_rx__tlrb_aib_phy';out=root/'campaign';target=out/'cases'/design
            work=target/'attempt_2/work';work.mkdir(parents=True)
            for name in ('source_audit.json','conditional_include_evidence.json'):(work/name).write_text('{}')
            (work/'flow.log').write_text('[ERROR PDN-0185] Insufficient width (12.42 um) to add straps on layer met4 in grid "grid" with total strap width 15.2 um and offset 13.6 um.\n')
            old=dict(design=design,baseline=str(base),status='not_clean',policy_revision=17,
                baseline_hashes={n:campaign.sha(base/n) for n in ('config.mk','constraint.sdc','result.json')},
                attempts=[dict(work=str(work),status='fail',strategy='source_normalization',implementation_sha256={n:campaign.sha(work/n) for n in ('source_audit.json','conditional_include_evidence.json')})])
            campaign.save(target/'repair.json',old)
            case=dict(design=design,run=str(base),category='frontend_header_as_source',stage='1_1_yosys_canonicalize',drc=None,lvs=None,errors=[])
            self.assertTrue(campaign.reviewed_source_pdn_retry(case,old))
            self.assertFalse(campaign.reviewed_source_pdn_retry(dict(case,design='C99999__other'),old))
            captured=[]
            def capture(work,*args,**kwargs):
                cfg=(work/'config.mk').read_text();captured.append(cfg)
                self.assertEqual(campaign.mk(cfg,'DIE_AREA'),'0 0 36 36')
                self.assertEqual(campaign.mk(cfg,'CORE_AREA'),'2 2 34 34')
                self.assertEqual(campaign.mk(cfg,'CORE_UTILIZATION'),'')
                self.assertNotIn('PDN_TCL',cfg)
                self.assertEqual((work/'constraint.sdc').read_bytes(),(base/'constraint.sdc').read_bytes())
                raise RuntimeError('fixture ends before physical execution')
            with patch.object(campaign,'source_repair',side_effect=lambda w,c:(c,[])),patch.object(campaign,'podman',side_effect=capture):
                result=campaign.run_case(case,out,None,None,None)
            self.assertEqual(len(captured),1);self.assertTrue(result['baseline_unchanged'])
            self.assertEqual(json.loads((target/('repair_before_policy_v'+str(campaign.POLICY_REVISION)+'.json')).read_text()),old)


if __name__=='__main__':unittest.main()
