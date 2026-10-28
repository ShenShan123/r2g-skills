import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import run_campaign as campaign


# Real campaign inputs are archived separately from the published tool repository.
@unittest.skipUnless((Path(__file__).parent / 'fpu_package_order_evidence.json').is_file(),
                     "requires archived SKY130 campaign evidence (not shipped in Git)")
class FpuPackageOrderTests(unittest.TestCase):
    def test_original_sources_and_config_are_required_for_all_three_tops(self):
        here=Path(__file__).parent;fixture=here/'semantic_evidence/fpu_original_review'
        evidence=json.loads((here/'fpu_package_order_evidence.json').read_text())
        original=fixture/'package/sources'/evidence['repository']
        for cid,item in evidence['cases'].items():
            for change in (None,'config','package','source_set'):
                with self.subTest(case=cid,change=change),tempfile.TemporaryDirectory() as directory:
                    work=Path(directory)
                    for rel in item['source_sha256']:
                        target=work/rel;target.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copy2(original/Path(rel).relative_to('rtl'),target)
                    baseline=next((fixture/'baseline').glob(cid+'*/config.mk')).read_text()
                    cfg=campaign.set_mk(baseline,'VERILOG_FILES',' '.join('/work/'+p for p in item['original_order']))
                    if change=='config':baseline+='export VERILOG_DEFINES = -DHALFPREC\n'
                    if change=='package':
                        p=work/item['packages'][0];p.write_bytes(p.read_bytes()+b'\n// changed package\n')
                    if change=='source_set':cfg=campaign.set_mk(cfg,'VERILOG_FILES',' '.join('/work/'+p for p in item['original_order'][1:]))
                    if change:
                        with self.assertRaises(ValueError):campaign.order_reviewed_fpu_packages(work,item['design'],baseline,cfg)
                        self.assertFalse((work/'source_order_evidence.json').exists())
                    else:
                        result=campaign.order_reviewed_fpu_packages(work,item['design'],baseline,cfg)
                        self.assertEqual(campaign.mk(result,'VERILOG_FILES').split(),['/work/'+p for p in item['ordered_sources']])
                        self.assertEqual(set(item['original_order']),set(item['ordered_sources']))
                        for rel,digest in item['source_sha256'].items():self.assertEqual(campaign.sha(work/rel),digest)
                        for key in ('SDC_FILE','CORE_UTILIZATION','SYNTH_MEMORY_MAX_BITS','ABC_AREA'):
                            self.assertEqual(campaign.mk(cfg,key),campaign.mk(result,key))

    def test_campaign_uses_original_rtl_ordering_without_translation(self):
        here=Path(__file__).parent;fixture=here/'semantic_evidence/fpu_original_review'
        document=json.loads((here/'fpu_package_order_evidence.json').read_text());item=document['cases']['C03964']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);baseline=root/'baseline';shutil.copytree(next((fixture/'baseline').glob('C03964*')),baseline)
            case=dict(design=item['design'],run=str(baseline),category='frontend_systemverilog',stage='1_1_yosys_canonicalize',drc=None,lvs=None,errors=['Failed to detect width for parameter \\C_OP!'])
            def source(work,cfg):
                for rel in item['source_sha256']:
                    p=work/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(fixture/'package/sources'/document['repository']/Path(rel).relative_to('rtl'),p)
                return campaign.set_mk(cfg,'VERILOG_FILES',' '.join('/work/'+p for p in item['original_order'])),[]
            def stop(work,*args,**kwargs):
                self.assertTrue((work/'source_order_evidence.json').is_file())
                self.assertFalse((work/'slang.v').exists())
                self.assertEqual((work/'constraint.sdc').read_bytes(),(baseline/'constraint.sdc').read_bytes())
                raise RuntimeError('fixture stops before physical flow')
            with patch.object(campaign,'source_repair',side_effect=source),patch.object(campaign,'translate',side_effect=AssertionError('unexpected compiler translation')),patch.object(campaign,'podman',side_effect=stop):
                result=campaign.run_case(case,root/'campaign',None,None,None)
            self.assertEqual(result['attempts'][0]['strategy'],'source_package_order')
            self.assertTrue(result['baseline_unchanged'])


if __name__=='__main__':unittest.main()
