import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_campaign as campaign


class ReviewedAreaFeedthroughTests(unittest.TestCase):
    def test_compound_retry_preserves_smallest_area_and_original_constraints(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);baseline=root/'baseline';baseline.mkdir()
            (baseline/'config.mk').write_text('export DESIGN_NAME = b2bcd\nexport DIE_AREA = 0 0 54 54\nexport CORE_AREA = 2 2 52 52\n')
            (baseline/'constraint.sdc').write_text('original constraints\n');(baseline/'result.json').write_text('{}')
            design='C00435__b2bcd__verilog_fundamentals';out=root/'campaign';target=out/'cases'/design
            work=target/'attempt_1/work';work.mkdir(parents=True);run=target/'attempt_1/run';run.mkdir()
            netlist=run/'6_final.v';netlist.write_text('input [7:0] b; output [11:0] bcd; assign bcd[0] = b[0];')
            (work/'config.mk').write_text('reviewed first area config')
            proof=dict(design=design,prior_work=str(work),prior_run=str(run),
                       sha256={str(p):campaign.sha(p) for p in (netlist,work/'config.mk')})
            campaign.save(root/'c00435_compound_evidence.json',proof)
            hook=root/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl';hook.parent.mkdir(parents=True);hook.write_text('# hook')
            old=dict(design=design,baseline=str(baseline),status='not_clean',policy_revision=18,
                     baseline_hashes={n:campaign.sha(baseline/n) for n in ('config.mk','constraint.sdc','result.json')},
                     attempts=[dict(strategy=s,status='completed',work=str(work),run=str(run)) for s in ('area_expansion_1.25','area_expansion_1.50')])
            case=dict(design=design,run=str(baseline),category='placement_capacity',stage='5_1_grt',drc=None,lvs=None,errors=[])
            campaign.save(target/'repair.json',old)
            with patch.object(campaign,'HERE',root),patch.object(campaign,'inspect_run',return_value=dict(drc='clean',lvs='pin_mismatch')) as inspect:
                self.assertTrue(campaign.reviewed_area_feedthrough_retry(case,old))
                self.assertFalse(campaign.reviewed_area_feedthrough_retry(case,dict(old,policy_revision=20)))
                self.assertFalse(campaign.reviewed_area_feedthrough_retry(dict(case,design='C99999__other'),old))
                inspect.return_value=dict(drc='violations',lvs='pin_mismatch')
                self.assertFalse(campaign.reviewed_area_feedthrough_retry(case,old))
                inspect.return_value=dict(drc='clean',lvs='pin_mismatch')
                original=netlist.read_bytes();netlist.write_text('inout b;')
                with self.assertRaisesRegex(ValueError,'evidence changed'):campaign.reviewed_area_feedthrough_retry(case,old)
                netlist.write_bytes(original)
                seen=[]
                def capture(w,*args,**kwargs):
                    cfg=(w/'config.mk').read_text();seen.append(cfg)
                    self.assertEqual(campaign.mk(cfg,'DIE_AREA'),'0.0 0.0 68 68')
                    self.assertEqual(campaign.mk(cfg,'CORE_AREA'),'2.0 2.0 66.0 66.0')
                    self.assertEqual(campaign.mk(cfg,'POST_GLOBAL_PLACE_TCL'),'/work/feedthrough.tcl')
                    self.assertEqual((w/'constraint.sdc').read_bytes(),(baseline/'constraint.sdc').read_bytes())
                    self.assertEqual((w/'feedthrough.tcl').read_bytes(),hook.read_bytes())
                    raise RuntimeError('fixture stops before physical execution')
                with patch.object(campaign,'podman',side_effect=capture):result=campaign.run_case(case,out,None,None,None)
                self.assertEqual(len(seen),1);self.assertTrue(result['baseline_unchanged'])
                self.assertEqual(json.loads((target/f'repair_before_policy_v{campaign.POLICY_REVISION}.json').read_text()),old)


if __name__=='__main__':unittest.main()
