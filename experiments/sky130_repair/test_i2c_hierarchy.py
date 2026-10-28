import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

import run_campaign as campaign

HERE=Path(__file__).parent
FIXTURE=HERE/'semantic_evidence/c03259_tristate_review/hierarchical_probe'


# Real campaign inputs are archived separately from the published tool repository.
@unittest.skipUnless((Path(__file__).parent / 'semantic_evidence/c03259_tristate_review/hierarchical_probe/database_preflight/flattened.v').is_file(),
                     "requires archived SKY130 campaign evidence (not shipped in Git)")
class I2CHierarchyTests(unittest.TestCase):
    def test_actual_flattened_netlist_and_reject_corruptions(self):
        original=(FIXTURE/'database_preflight/flattened.v').read_text()
        variants={
            'original':original,
            'direction':original.replace('inout sda;','output sda;'),
            'missing_driver':re.sub(r'sky130_fd_sc_hd__ebufn_1\s+\S+\s*\(.*?\);','',original,count=1,flags=re.S),
            'disabled_enable':re.sub(r'\.TE_B\([^)]*\)',".TE_B(1'b1)",original,count=1),
            'different_driver':original.replace('sky130_fd_sc_hd__ebufn_1','sky130_fd_sc_hd__buf_1',1),
            'alias':original.replace('endmodule','assign sda = clk;\nendmodule'),
        }
        for name,text in variants.items():
            with self.subTest(name=name),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);path=root/'final.v';path.write_text(text)
                log=root/'flow.log';log.write_text('R2G_I2C_FINAL_TOPOLOGY_PASS R2G_I2C_FINAL_POWER_PASS')
                if name=='original':
                    self.assertEqual(len(campaign.validate_i2c_final_netlist(path,log)['drivers']),2)
                    log.write_text('report skipped')
                else:self.assertNotEqual(text,original)
                with self.assertRaises(ValueError):campaign.validate_i2c_final_netlist(path,log)

    def test_original_inputs_and_actual_candidate_evidence_required(self):
        for change in (None,'source','config','sdc','candidate','hook','master_negative','slave_negative','frozen_output','design','bus_control','bus_positive','bus_traffic','bus_negative','bus_hook','power_positive','power_negative','power_hook'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);tools=root/'tools';tools.mkdir();work=root/'work';work.mkdir();package=root/'package'
                evidence=json.loads((HERE/'c03259_hierarchy_evidence_v27.json').read_text())
                for rel in evidence['original_source_sha256']:
                    target=package/rel;target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(FIXTURE.parent/'rtl'/Path(rel).name,target)
                for name in ('c03259_hierarchy_evidence_v27.json',*evidence['candidate_files_sha256']):shutil.copy2(HERE/name,tools/name)
                shutil.copy2(FIXTURE/'frozen_synthesis/original_constraint.sdc',work/'constraint.sdc')
                cfg=(FIXTURE/'frozen_synthesis/original_config.mk').read_text();design=evidence['design']
                if change=='source':next(package.rglob('I2C_master.v')).write_text('module I2C_master; endmodule')
                if change=='config':cfg+='export SYNTH_GUT = 1\n'
                if change=='sdc':(work/'constraint.sdc').write_text('create_clock -period 1000 [get_ports clk]\n')
                if change=='candidate':(tools/'c03259_hierarchical_mapped.v').write_text('module I2C_module; endmodule')
                if change=='hook':(tools/'check_i2c_tristate_topology.tcl').write_text('# check skipped\n')
                if change=='power_positive':evidence['power_hook_preflight']['results']['6_final']['returncode']=1
                if change=='power_negative':evidence['final_power_guard']['results']['missing_power_negative']['returncode']=0
                if change=='power_hook':(tools/'connect_i2c_driver_power.tcl').write_text('# omitted\n')
                if change=='bus_hook':(tools/'preserve_i2c_shared_bus.tcl').write_text('# preservation skipped\n')
                if change=='bus_control':evidence['shared_bus_preservation']['results']['control']['returncode']=0
                if change=='bus_positive':evidence['shared_bus_preservation']['results']['preserved']['returncode']=1
                if change=='bus_traffic':evidence['post_route_traffic']['results']['positive']['returncode']=1
                if change=='bus_negative':evidence['post_route_traffic']['results']['negative']['returncode']=0
                if change=='design':design='C00000__other__case'
                if change=='master_negative':evidence['traffic'][1]['results']['negative']['returncode']=0
                if change=='slave_negative':evidence['slave_negative']['detected']=False
                if change=='frozen_output':evidence['traffic'][1]['mapped_sha256']='0'*64
                (tools/'c03259_hierarchy_evidence_v27.json').write_text(json.dumps(evidence))
                with patch.object(campaign,'HERE',tools):
                    if change:
                        with self.assertRaises(ValueError):campaign.prepare_i2c_hierarchy(work,design,cfg,source_package=package)
                        self.assertFalse((work/'i2c_hierarchy_evidence.json').exists())
                    else:
                        result=campaign.prepare_i2c_hierarchy(work,design,cfg,source_package=package)
                        self.assertEqual(campaign.mk(result,'SYNTH_KEEP_MODULES'),'*I2C_master* *I2C_slave*')
                        for key in ('CORE_UTILIZATION','ABC_AREA','SYNTH_MEMORY_MAX_BITS','SDC_FILE','VERILOG_DEFINES'):
                            self.assertEqual(campaign.mk(result,key),campaign.mk(cfg,key))
                        self.assertEqual(len(list((work/'rtl').glob('*.v'))),3)
                        self.assertEqual(campaign.mk(result,'PRE_GLOBAL_ROUTE_TCL'),'/work/preserve_i2c_shared_bus.tcl')
                        self.assertEqual(campaign.mk(result,'PRE_PDN_TCL'),'/work/connect_i2c_driver_power.tcl')
                        self.assertEqual(campaign.mk(result,'PRE_FINAL_REPORT_TCL'),'/work/check_i2c_final_power.tcl')


if __name__=='__main__':unittest.main()
