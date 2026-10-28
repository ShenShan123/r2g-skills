#!/usr/bin/env python3
"""Check candidate connectivity on the retained real DEF, without optimization."""
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess

HERE=Path(__file__).resolve().parent


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    root=Path('/tmp/sky130_aux208');case='C03490__zube_wrapper__zube'
    baseline=root/'baseline'/(case+'__sky130hd__p5')
    out=HERE/'semantic_evidence/c03490_driver_replication/db_preflight';out.mkdir(exist_ok=False)
    lib=root/'environment/miniconda3/envs/eda/share/pdk/sky130A/libs.ref/sky130_fd_sc_hd'
    executable=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad')
    hook=HERE/'replicate_zube_inout_drivers.tcl'
    base=f'read_lef {lib}/techlef/sky130_fd_sc_hd__nom.tlef\nread_lef {lib}/lef/sky130_fd_sc_hd.lef\nread_def {baseline}/6_final.def\nset block [ord::get_db_block]\n'
    dump='''proc dump_connectivity {path} {
 set f [open $path w];set b [ord::get_db_block]
 foreach i [$b getInsts] {
  puts $f [join [list INST [$i getName] [[$i getMaster] getName] [$i getOrient] [$i getLocation] [$i getPlacementStatus]] "\t"]
  foreach t [$i getITerms] {
   set net [$t getNet];set name NULL;if {$net ne "NULL"} {set name [$net getName]}
   puts $f [join [list ITERM [$i getName] [[$t getMTerm] getName] $name] "\t"]
  }
 }
 foreach t [$b getBTerms] {puts $f [join [list BTERM [$t getName] [$t getIoType] [[$t getNet] getName]] "\t"]}
 close $f
}
'''
    cpu=min(os.sched_getaffinity(0))
    def limits():
        os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(60,60));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
    def run(name,text):
        script=out/(name+'.tcl');script.write_text(base+text)
        with (out/(name+'.log')).open('w') as log:
            p=subprocess.run([str(executable),'-exit','-no_init',str(script)],stdout=log,stderr=log,preexec_fn=limits,timeout=65,env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1'))
        return p.returncode,(out/(name+'.log')).read_text()
    rc,log=run('positive',dump+f'dump_connectivity {out}/before.tsv\nsource {hook}\ndump_connectivity {out}/after.tsv\nsource {hook}\ndump_connectivity {out}/twice.tsv\nwrite_verilog {out}/replicated.v\n')
    if rc:raise ValueError('actual DEF positive preflight failed')
    before=set((out/'before.tsv').read_text().splitlines());after=set((out/'after.tsv').read_text().splitlines())
    assert after==set((out/'twice.tsv').read_text().splitlines()),'hook is not idempotent'
    removed=before-after;added=after-before
    expected_removed={f'BTERM\tio_oeb[{i}]\tINOUT\tio_oeb[8]' for i in range(9,16)}
    assert removed==expected_removed,'unexpected original cell, geometry or connection mutation'
    for i in range(9,16):
        assert f'BTERM\tio_oeb[{i}]\tINOUT\tio_oeb[{i}]' in added
        assert f'ITERM\tr2g_zube_oeb_inv_{i}\tA\tio_out[16]' in added
        assert f'ITERM\tr2g_zube_oeb_inv_{i}\tY\tio_oeb[{i}]' in added
    new_instances=[l for l in added if l.startswith('INST\t')]
    assert len(new_instances)==7 and all('\tsky130_fd_sc_hd__inv_1\t' in l for l in new_instances)
    assert all(l.startswith(('BTERM\tio_oeb[','INST\tr2g_zube_oeb_inv_','ITERM\tr2g_zube_oeb_inv_')) for l in added)
    negatives={
        'port_direction':'set bt [$block findBTerm {io_oeb[9]}];$bt setIoType INPUT\n',
        'control_source':'set i [$block findInst _4431_];set pin [$i findITerm A];$pin disconnect;$pin connect [[$block findBTerm clk] getNet]\n',
        'extra_internal_load':'set master [[ord::get_db] findMaster sky130_fd_sc_hd__inv_1];set i [odb::dbInst_create $block $master negative_load];[$i findITerm A] connect [[$block findBTerm {io_oeb[8]}] getNet]\n',
    }
    rejection={}
    for name,mutation in negatives.items():
        code,text=run(name,mutation+f'source {hook}\n')
        assert code!=0 and 'zube driver replication:' in text,'negative was not rejected by candidate gate'
        rejection[name]=dict(returncode=code,rejected=True)
    report=dict(design=case,original_def_sha256=sha(baseline/'6_final.def'),original_config_sha256=sha(baseline/'config.mk'),
                hook_sha256=sha(hook),openroad_sha256=sha(executable),original_cell_and_pin_connections_preserved_except_seven_oeb_ports=True,
                original_placement_preserved=True,all_port_directions_unchanged=True,added_inverters=7,idempotent=True,negative_controls=rejection,
                files={p.name:sha(p) for p in out.iterdir() if p.is_file()},physical_execution=False,
                scope='Actual retained DEF topology preflight only; no placement, routing, extraction or signoff executed')
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='files'}))


if __name__=='__main__':main()
