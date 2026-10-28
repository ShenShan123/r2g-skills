import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_campaign as campaign


class ReviewedFilterPinRetryTests(unittest.TestCase):
    def test_requires_completed_clean_lvs_and_unchanged_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'baseline';base.mkdir()
            design='C03391__q_lpf_decim_axi__2026_g_periodic-signal-measurement-and-a'
            for name,text in [('config.mk','export DESIGN_NAME = q_lpf_decim_axi\nexport CORE_UTILIZATION = 50\n'),
                              ('constraint.sdc','original clocks\n'),('result.json','{}')]:
                (base/name).write_text(text)
            out=root/'campaign';target=out/'cases'/design;work=target/'attempt_1/work';work.mkdir(parents=True)
            run=target/'attempt_1/run';run.mkdir()
            original=root/'original';original.mkdir();(original/'top.v').write_text('original RTL')
            private=work/'rtl';private.mkdir();(private/'top.v').write_bytes((original/'top.v').read_bytes())
            audit=dict(status='source_preservation_checked',
                       files=[dict(path=str(original/'top.v'),original_sha256=campaign.sha(original/'top.v'),
                                   private_sha256=campaign.sha(private/'top.v'))])
            campaign.save(work/'source_audit.json',audit)
            (run/'6_final.def').write_text('reviewed pin and PDN geometry')
            (run/'6_drc.lyrdb').write_text('<report-database><items>'+"<item><category>'m3.2'</category></item>"*2+'</items></report-database>')
            proof=dict(design=design,prior_work=str(work),prior_run=str(run),source_audit=audit,
                       original_root=str(original),private_root=str(private),
                       sha256={str(p):campaign.sha(p) for p in [work/'source_audit.json',run/'6_final.def',run/'6_drc.lyrdb']})
            campaign.save(root/'c03391_pin_geometry_evidence.json',proof)
            (root/'pin_pdn_clearance.tcl').write_text('# retained hook\n')
            old=dict(design=design,baseline=str(base),status='not_clean',policy_revision=1,
                     baseline_hashes={n:campaign.sha(base/n) for n in ('config.mk','constraint.sdc','result.json')},
                     attempts=[dict(strategy='source_normalization',status='completed',work=str(work),run=str(run))])
            case=dict(design=design,run=str(base),category='frontend_header_as_source',stage='1_1_yosys_canonicalize',drc=None,lvs=None,errors=[])
            verdict=dict(lvs='clean',drc_items=None,dual_clean=False)
            with patch.object(campaign,'HERE',root),patch.object(campaign,'inspect_run',return_value=verdict) as inspect:
                self.assertTrue(campaign.reviewed_source_pin_retry(case,old))
                for status in ('preparing','dual_clean'):
                    self.assertFalse(campaign.reviewed_source_pin_retry(case,dict(old,status=status)))
                running=dict(old,attempts=[dict(old['attempts'][0],status='signoff_running')])
                self.assertFalse(campaign.reviewed_source_pin_retry(case,running))
                inspect.return_value=dict(verdict,lvs='pin_mismatch')
                self.assertFalse(campaign.reviewed_source_pin_retry(case,old))
                inspect.return_value=verdict
                (private/'top.v').write_text('mutated RTL')
                with self.assertRaisesRegex(ValueError,'RTL changed'):campaign.reviewed_source_pin_retry(case,old)
                (private/'top.v').write_bytes((original/'top.v').read_bytes())
                (run/'6_final.def').write_text('changed geometry')
                with self.assertRaisesRegex(ValueError,'evidence changed'):campaign.reviewed_source_pin_retry(case,old)
                (run/'6_final.def').write_text('reviewed pin and PDN geometry')
                campaign.save(target/'repair.json',old)
                captured=[]
                def capture(w,*args,**kwargs):
                    cfg=(w/'config.mk').read_text();captured.append(cfg)
                    self.assertEqual(campaign.mk(cfg,'CORE_UTILIZATION'),'50')
                    self.assertEqual(campaign.mk(cfg,'PRE_IO_PLACEMENT_TCL'),'/work/pin_pdn_clearance.tcl')
                    self.assertNotIn('PDN_TCL',cfg)
                    self.assertEqual((w/'constraint.sdc').read_bytes(),(base/'constraint.sdc').read_bytes())
                    self.assertEqual((w/'pin_geometry_evidence.json').read_bytes(),(root/'c03391_pin_geometry_evidence.json').read_bytes())
                    raise RuntimeError('fixture stops before physical execution')
                with patch.object(campaign,'source_repair',side_effect=lambda w,c:(c,[])),patch.object(campaign,'podman',side_effect=capture):
                    result=campaign.run_case(case,out,None,None,None)
                self.assertEqual(len(captured),1);self.assertTrue(result['baseline_unchanged'])
                self.assertEqual(json.loads((target/f'repair_before_policy_v{campaign.POLICY_REVISION}.json').read_text()),old)


if __name__=='__main__':unittest.main()
