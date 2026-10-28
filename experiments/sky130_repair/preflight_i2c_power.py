"""Check supply restoration on real pre-PDN and final DBs without changing originals."""
from pathlib import Path
import hashlib,json,subprocess,resource,os
h=Path(__file__).resolve().parent;o=h/'semantic_evidence/c03259_tristate_review/power_hook_preflight';o.mkdir(exist_ok=False)
w=Path('/tmp/sky130_aux208/results/cases/C03259__I2C_module__Serial-Communication-Protocols/attempt_3/work/results/sky130hd/I2C_module/base');exe=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad');sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def limits():
 os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});resource.setrlimit(resource.RLIMIT_CPU,(30,30));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
body='''proc snapshot_signals {} {
 set out {}
 foreach i [[ord::get_db_block] getInsts] {
  foreach t [$i getITerms] {
   set p [$t getMTerm]
   if {[$p getSigType] in {POWER GROUND}} {continue}
   set n [$t getNet];set nn NULL;if {$n ne "NULL"} {set nn [$n getName]}
   lappend out [list [$i getName] [$p getName] $nn]
  }
 }
 return [lsort $out]
}
set before [snapshot_signals]
'''
results={}
for stage in ['2_3_floorplan_tapcell','6_final']:
 script=o/(stage+'.tcl');script.write_text(f'read_db {w}/{stage}.odb\n'+body+f'source {h}/connect_i2c_driver_power.tcl\n'+'''if {$before ne [snapshot_signals]} {error "Signal connectivity changed"}
puts R2G_ALL_SIGNAL_CONNECTIONS_UNCHANGED
'''+f'write_db {o}/{stage}.odb\nexit\n')
 with (o/(stage+'.log')).open('w') as f:r=subprocess.run([str(exe),'-exit',str(script)],stdout=f,stderr=f,preexec_fn=limits,timeout=35)
 results[stage]={'returncode':r.returncode,'input_sha256':sha(w/(stage+'.odb')),'log':(o/(stage+'.log')).read_text()};print(stage,results[stage]['log'],flush=True)
(o/'result.json').write_text(json.dumps({'results':results,'hook_sha256':sha(h/'connect_i2c_driver_power.tcl'),'scope':'Only eight PG/body connections restored on actual DB copies; all signal ITerm connections identical; not a signoff result'},indent=2)+'\n')
