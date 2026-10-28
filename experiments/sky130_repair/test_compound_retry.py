"""Guard the explicit post-frontend retry and its simulation evidence binding."""
import hashlib
import importlib.util
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/'agent'))
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('campaign_retry_test',HERE/'run_campaign.py')
campaign=importlib.util.module_from_spec(spec);spec.loader.exec_module(campaign)


class CompoundRetryTest(unittest.TestCase):
    @unittest.skipUnless((HERE / "baseline_inventory_20261026.json").is_file(),
                         "requires archived SKY130 baseline inventory (not shipped in Git)")
    def test_queue_preserves_inventory_and_does_not_treat_unknown_size_as_zero(self):
        inventory=json.loads((HERE/'baseline_inventory_20261026.json').read_text())['failures']
        ordered=campaign.order_cases(inventory)
        self.assertEqual(sorted(c['design'] for c in ordered),sorted(c['design'] for c in inventory))
        self.assertEqual(len(ordered),92)
        positions={c['design'].split('__')[0]:i for i,c in enumerate(ordered)}
        # 90-second completed physical run before unknown-cost frontend failure.
        self.assertLess(positions['C00953'],positions['C03391'])
        self.assertLess(positions['C02514'],positions['C03391'])
        self.assertEqual(campaign.order_cases(list(reversed(inventory))),ordered)
        canary=campaign.order_cases([c for c in inventory if c['design'].split('__')[0] in ('C00303','C02098','C00241','C02620')],True)
        self.assertEqual([c['design'].split('__')[0] for c in canary],['C00303','C02098','C00241','C02620'])

    def test_proofs_bind_both_original_and_actual_translation(self):
        for cid,changed in itertools.product(('C03966','C03962','C03986','C03980'),(None, 'source', 'translation')):
            with self.subTest(case=cid,changed=changed), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);base=root/'baseline';base.mkdir();tools=root/'tools';tools.mkdir()
                cfg='export DESIGN_NAME = rv_plic_target\nexport PLATFORM = sky130hd\n'
                for filename,text in [('config.mk',cfg),('constraint.sdc','original constraints'),('result.json','{}')]:
                    (base/filename).write_text(text)
                evidence=dict(results=[dict(returncode=0,proven_outputs=7),dict(returncode=1)],
                    source_sha256=hashlib.sha256(b'original source').hexdigest(),
                    sha256={'slang.v':hashlib.sha256(b'reviewed translation').hexdigest()})
                if cid!='C03966':
                    evidence['source_sha256']={'rtl/rtl/rv_plic_target.sv':evidence['source_sha256']}
                    evidence['results'][0]['equivalence_cells']=7
                if cid=='C03980':
                    evidence['results']=[dict(build_returncode=0,simulation_returncode=0),dict(build_returncode=0,simulation_returncode=-6)]
                    evidence['negative_control_detected']=True
                (tools/(cid.lower()+'_translation_evidence.json')).write_text(json.dumps(evidence))
                case=dict(design=cid+'__rv_plic_target__fixture',run=str(base),
                    category='frontend_systemverilog',stage='1_1_yosys_canonicalize',drc=None,lvs=None,errors=[])
                def translate(work,cfg,top,tag):
                    source=work/'rtl/rtl/rv_plic_target.sv';source.parent.mkdir(parents=True)
                    source.write_bytes(b'changed source' if changed=='source' else b'original source')
                    (work/'slang.v').write_bytes(b'changed translation' if changed=='translation' else b'reviewed translation')
                    return campaign.set_mk(cfg,'VERILOG_FILES','/work/slang.v')
                started=[]
                def capture(work,*args,**kwargs):
                    started.append(json.loads((work/'translation_evidence.json').read_text()))
                    self.assertEqual((work/'constraint.sdc').read_text(),'original constraints')
                    raise RuntimeError('fixture stops before physical execution')
                with patch.object(campaign,'HERE',tools),patch.object(campaign,'source_repair',side_effect=lambda w,c:(c,[])),patch.object(campaign,'translate',side_effect=translate),patch.object(campaign,'podman',side_effect=capture):
                    result=campaign.run_case(case,root/'campaign',None,None,None)
                self.assertTrue(result['baseline_unchanged'])
                if changed:
                    self.assertFalse(started)
                    self.assertIn('differs from the reviewed',result['reason'])
                else:
                    self.assertEqual(started,[evidence])
                    expected=('bounded_two_state_traffic_simulation_not_formal' if cid=='C03980' else
                              'independent_frontend_output_equivalence_default_configuration' if cid=='C03966'
                              else 'independent_frontend_equivalence_default_configuration')
                    self.assertEqual(result['attempts'][0]['semantic_review'],expected)

    def run_fixture(self, signature=True, good_evidence=True):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/'baseline';base.mkdir();tools=root/'tools';tools.mkdir()
            cfg='export DESIGN_NAME = tiny_tpu_top\nexport PLATFORM = sky130hd\nexport CORE_UTILIZATION = 50\n'
            for name,text in [('config.mk',cfg),('constraint.sdc','create_clock -period 5 [get_ports clk]\n'),('result.json','{}')]:
                (base/name).write_text(text)
            name='C02511__tiny_tpu_top__tiny-tpu';out=root/'campaign';target=out/'cases'/name;oldwork=target/'attempt_1/work';oldwork.mkdir(parents=True)
            (oldwork/'flow.log').write_text('[ERROR PPL-0024] Number of IO pins (1703) exceeds maximum' if signature else '[ERROR GPL-1234] unrelated placement failure')
            old=dict(design=name,baseline=str(base),status='not_clean',policy_revision=1,
                baseline_hashes={n:campaign.sha(base/n) for n in ('config.mk','constraint.sdc','result.json')},
                attempts=[dict(work=str(oldwork),strategy='sv_frontend',failure_class='place_failed')])
            campaign.save(target/'repair.json',old)
            evidence=dict(results=[{'returncode':0},{'returncode':-6}],sha256={'slang.v':hashlib.sha256(b'reviewed translation').hexdigest()})
            (tools/'c02511_translation_evidence.json').write_text(json.dumps(evidence))
            case=dict(design=name,run=str(base),category='frontend_systemverilog',stage='1_1_yosys_canonicalize',drc=None,lvs=None,errors=[])
            observed=[]
            def translate(work,cfg,top,tag):
                (work/'slang.v').write_bytes(b'reviewed translation' if good_evidence else b'different translation')
                return campaign.set_mk(cfg,'VERILOG_FILES','/work/slang.v')
            def capture(work,*args,**kwargs):
                observed.append((work/'config.mk').read_text())
                raise RuntimeError('fixture stops before physical execution')
            with patch.object(campaign,'HERE',tools),patch.object(campaign,'source_repair',side_effect=lambda w,c:(c,[])),patch.object(campaign,'translate',side_effect=translate),patch.object(campaign,'podman',side_effect=capture):
                result=campaign.run_case(case,out,None,None,None)
            return result,observed,(target/('repair_before_policy_v'+str(campaign.POLICY_REVISION)+'.json')).exists(),old

    def test_only_matching_io_capacity_failure_is_retried(self):
        result,observed,archived,old=self.run_fixture(signature=False)
        self.assertEqual(result,old);self.assertFalse(observed);self.assertFalse(archived)

    def test_retry_keeps_translation_and_expands_area_with_original_sdc(self):
        result,observed,archived,_=self.run_fixture()
        self.assertTrue(archived);self.assertEqual(len(observed),1)
        self.assertEqual(campaign.mk(observed[0],'CORE_UTILIZATION'),'32.0')
        self.assertEqual(campaign.mk(observed[0],'VERILOG_FILES'),'/work/slang.v')
        self.assertEqual(result['attempts'][0]['semantic_review'],'bounded_differential_simulation_passed_not_formal')
        self.assertTrue(result['baseline_unchanged'])

    def test_changed_translation_never_starts_physical_execution(self):
        result,observed,archived,_=self.run_fixture(good_evidence=False)
        self.assertTrue(archived);self.assertFalse(observed)
        self.assertEqual(result['status'],'blocked')
        self.assertIn('translation/source differs',result['reason'])
        self.assertTrue(result['baseline_unchanged'])


if __name__=='__main__':unittest.main()
