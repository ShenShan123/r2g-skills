import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import run_campaign as campaign


# Real campaign inputs are archived separately from the published tool repository.
@unittest.skipUnless((Path(__file__).parent / 'c03490_driver_replication_evidence.json').is_file(),
                     "requires archived SKY130 campaign evidence (not shipped in Git)")
class ZubeDriverReplicationTests(unittest.TestCase):
    def test_final_actual_netlist_matches_original_cone(self):
        fixture=Path(__file__).parent/'semantic_evidence/c03490_driver_replication/db_preflight/replicated.v'
        result=campaign.validate_zube_final_netlist(fixture)
        self.assertEqual(result['independent_inout_drivers'],8)
        self.assertTrue(result['inout_interfaces_preserved'])

    def test_later_optimizer_cannot_reconnect_or_buffer_resolved_outputs(self):
        fixture=Path(__file__).parent/'semantic_evidence/c03490_driver_replication/db_preflight/replicated.v'
        original=fixture.read_text()
        mutations=[original.replace('.A(io_out[16])','.A(io_oeb[8])',1),
                   original.replace('inout [27:0] io_oeb;','output [27:0] io_oeb;',1),
                   original.replace('endmodule','assign io_oeb[10] = io_oeb[8];\nendmodule',1),
                   original.replace('sky130_fd_sc_hd__inv_1 r2g_zube_oeb_inv_9','sky130_fd_sc_hd__buf_4 r2g_zube_oeb_inv_9',1)]
        for text in mutations:
            self.assertNotEqual(original,text)
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'final.v';path.write_text(text)
                with self.assertRaises(ValueError):campaign.validate_zube_final_netlist(path)

    def test_exact_original_sources_configuration_and_proof_are_required(self):
        here=Path(__file__).parent;fixture=here/'semantic_evidence/c03490_driver_replication'
        for change in (None,'macro','source','hook','proof','design'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);tools=root/'tools';tools.mkdir();work=root/'work';work.mkdir()
                package=root/'package';shutil.copytree(fixture/'original_package',package)
                for name in ('c03490_driver_replication_evidence.json','replicate_zube_inout_drivers.tcl'):shutil.copy2(here/name,tools/name)
                cfg=(fixture/'baseline/config.mk').read_text();design='C03490__zube_wrapper__zube'
                if change=='macro':cfg+='export VERILOG_DEFINES = -DFORMAL\n'
                if change=='source':
                    path=package/'sources/R01908__thejpster__zube/src/zube_wrapper.v';path.write_text(path.read_text().replace("8'hFF","8'h00"))
                if change=='hook':
                    path=tools/'replicate_zube_inout_drivers.tcl';path.write_text(path.read_text()+'\n# altered\n')
                if change=='proof':
                    path=tools/'c03490_driver_replication_evidence.json';e=json.loads(path.read_text());e['cone_proof']['negative_mismatches']=0;path.write_text(json.dumps(e))
                if change=='design':design='C00000__other__design'
                with patch.object(campaign,'HERE',tools):
                    if change:
                        with self.assertRaises(ValueError):campaign.prepare_zube_driver_replication(work,design,cfg,source_package=package)
                        self.assertFalse((work/'driver_replication_evidence.json').exists())
                    else:
                        result=campaign.prepare_zube_driver_replication(work,design,cfg,source_package=package)
                        self.assertEqual(campaign.mk(result,'POST_GLOBAL_PLACE_TCL'),'/work/replicate_zube_inout_drivers.tcl')
                        for key in ('VERILOG_FILES','VERILOG_DEFINES','SDC_FILE','DIE_AREA','CORE_AREA'):
                            self.assertEqual(campaign.mk(result,key),campaign.mk(cfg,key))
                        self.assertEqual(campaign.sha(work/'replicate_zube_inout_drivers.tcl'),campaign.sha(here/'replicate_zube_inout_drivers.tcl'))

    def test_dispatch_retains_ports_rtl_and_original_constraints(self):
        here=Path(__file__).parent;fixture=here/'semantic_evidence/c03490_driver_replication'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);baseline=root/'baseline';shutil.copytree(fixture/'baseline',baseline)
            case=dict(design='C03490__zube_wrapper__zube',run=str(baseline),category='lvs_pin_mismatch',stage='final',drc='clean',lvs='pin_mismatch',errors=[])
            def stop(work,*args,**kwargs):
                cfg=(work/'config.mk').read_text()
                self.assertEqual(campaign.mk(cfg,'VERILOG_FILES'),campaign.mk((baseline/'config.mk').read_text(),'VERILOG_FILES'))
                self.assertEqual((work/'constraint.sdc').read_bytes(),(baseline/'constraint.sdc').read_bytes())
                self.assertTrue((work/'driver_replication_evidence.json').is_file())
                self.assertFalse((work/'feedthrough.tcl').exists())
                raise RuntimeError('fixture stops before physical execution')
            with patch.object(campaign,'PACKAGE',fixture/'original_package'),patch.object(campaign,'podman',side_effect=stop):
                result=campaign.run_case(case,root/'campaign',None,None,None)
            self.assertEqual(result['attempts'][0]['strategy'],'replicate_original_inout_drivers')
            self.assertEqual(set(result['attempts'][0]['config_delta']),{'POST_GLOBAL_PLACE_TCL'})
            self.assertIn('not_full_chip',result['attempts'][0]['semantic_review'])
            self.assertTrue(result['baseline_unchanged'])


if __name__=='__main__':unittest.main()
