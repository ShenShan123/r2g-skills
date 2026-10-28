from pathlib import Path
import subprocess,resource,os,json,hashlib
h=Path('/home/yangao/r2g-skills/experiments/sky130_repair');out=h/'semantic_evidence/c03259_tristate_review/dead_logic_preservation';out.mkdir(exist_ok=False);orfs=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts');mapped=h/'semantic_evidence/c03259_tristate_review/hierarchical_probe/frozen_synthesis/1_2_yosys.v';cpu=min(os.sched_getaffinity(0));sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
base=f'read_lef {orfs}/flow/platforms/sky130hd/lef/sky130_fd_sc_hd.tlef\nread_lef {orfs}/flow/platforms/sky130hd/lef/sky130_fd_sc_hd_merged.lef\nread_liberty {orfs}/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib\nread_verilog {mapped}\nlink_design I2C_module\n'
def limits():
 os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(30,30));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
results={}
for role,hook in [('control',''),('preserved',f'source {h}/preserve_i2c_tristate_drivers.tcl\n')]:
 script=out/(role+'.tcl');script.write_text(base+hook+'eliminate_dead_logic\n'+f'write_verilog {out}/{role}.v\nsource {h}/check_i2c_tristate_topology.tcl\nexit\n')
 with (out/(role+'.log')).open('w') as f:r=subprocess.run([str(orfs/'tools/install/OpenROAD/bin/openroad'),'-exit',str(script)],stdout=f,stderr=f,preexec_fn=limits,timeout=35)
 log=(out/(role+'.log')).read_text();results[role]=dict(returncode=r.returncode,log_tail=log[-1100:]);print(role,r.returncode,log[-900:])
report=dict(results=results,preservation_hook_sha256=sha(h/'preserve_i2c_tristate_drivers.tcl'),final_guard_sha256=sha(h/'check_i2c_tristate_topology.tcl'),mapped_sha256=sha(mapped),files={p.name:sha(p) for p in out.iterdir() if p.is_file()},physical_execution=False,scope='Exact frozen OpenROAD and LEFs; ordinary eliminate_dead_logic remains enabled')
(out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
