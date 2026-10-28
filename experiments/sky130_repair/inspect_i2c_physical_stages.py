from pathlib import Path
import subprocess,os,resource,json
h=Path('/home/yangao/r2g-skills/experiments/sky130_repair');out=h/'semantic_evidence/c03259_tristate_review/physical_attempt1_topology';out.mkdir(exist_ok=False)
work=Path('/tmp/sky130_aux208/results/cases/C03259__I2C_module__Serial-Communication-Protocols/attempt_1/work');base=work/'results/sky130hd/I2C_module/base';exe='/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad';cpu=min(os.sched_getaffinity(0))
body='''set block [ord::get_db_block]
set port [$block findBTerm sda]
set net [$port getNet]
puts "SDA_PORT [$port getIoType] net=[$net getName]"
foreach pin [$net getITerms] {puts "SDA_PIN [[$pin getInst] getName] [[$pin getMTerm] getName] [[$pin getMTerm] getIoType] [[[$pin getInst] getMaster] getName]"}
foreach inst [$block getInsts] {
 if {[string match *ebuf* [[$inst getMaster] getName]]} {
  puts "TRISTATE [$inst getName] [[$inst getMaster] getName]"
  foreach pin [$inst getITerms] {
   set n [$pin getNet];set nn NULL;if {$n ne "NULL"} {set nn [$n getName]}
   puts "TRISTATE_PIN [[$pin getMTerm] getName] [[$pin getMTerm] getIoType] $nn"
  }
 }
}
'''
def limits():
 os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(30,30));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
result={}
for stage in ('1_synth','2_1_floorplan','3_1_place_gp_skip_io','3_3_place_gp','3_4_place_resized','4_1_cts','5_1_grt','6_1_fill'):
 script=out/(stage+'.tcl');script.write_text(f'read_db {base}/{stage}.odb\n'+body+'exit\n')
 with (out/(stage+'.log')).open('w') as f:r=subprocess.run([exe,'-exit',str(script)],stdout=f,stderr=f,timeout=35,preexec_fn=limits)
 assert r.returncode==0,stage
 lines=[s for s in (out/(stage+'.log')).read_text().splitlines() if s.startswith(('SDA_','TRISTATE'))];result[stage]=lines;print(stage,'\n'+'\n'.join(lines))
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
