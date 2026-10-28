"""Bounded replay of failed CTS database; preserve shared bus, not acceptance rules."""
from pathlib import Path
import hashlib,json,os,resource,subprocess
h=Path(__file__).resolve().parent
out=h/'semantic_evidence/c03259_tristate_review/shared_bus_preservation'
out.mkdir(exist_ok=False)
orfs=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts')
base=Path('/tmp/sky130_aux208/results/cases/C03259__I2C_module__Serial-Communication-Protocols/attempt_2/work/results/sky130hd/I2C_module/base')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
cpu=min(os.sched_getaffinity(0))
def limits():
 os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(90,90));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
common=f'''read_liberty {orfs}/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
read_db {base}/4_cts.odb
read_sdc {base}/4_cts.sdc
source {orfs}/flow/platforms/sky130hd/setRC.tcl
set_thread_count 1
set_global_routing_layer_adjustment met1-met5 0.2
set_routing_layers -clock met3-met5
set_routing_layers -signal met1-met5
'''
result={}
for role,hook in [('control',''),('preserved','set_dont_touch [get_nets sda]\n')]:
 script=out/(role+'.tcl')
 script.write_text(common+hook+f'''source {h}/check_i2c_tristate_topology.tcl
pin_access
global_route -congestion_iterations 30
set_propagated_clock [all_clocks]
estimate_parasitics -global_routing
repair_design -verbose
source {h}/check_i2c_tristate_topology.tcl
write_verilog {out}/{role}.v
write_db {out}/{role}.odb
puts R2G_SHARED_BUS_REPAIR_PASS
exit
''')
 with (out/(role+'.log')).open('w') as log:
  p=subprocess.run([str(orfs/'tools/install/OpenROAD/bin/openroad'),'-exit',str(script)],stdout=log,stderr=log,preexec_fn=limits,timeout=100)
 text=(out/(role+'.log')).read_text();result[role]={'returncode':p.returncode,'tail':text[-1600:]}
 print(role,p.returncode,text[-800:],flush=True)
report={'results':result,'input_odb_sha256':sha(base/'4_cts.odb'),'input_sdc_sha256':sha(base/'4_cts.sdc'),'openroad_sha256':sha(orfs/'tools/install/OpenROAD/bin/openroad'),'scope':'Isolated original CTS database routing and repair diagnostic; no full signoff or acceptance change','files':{p.name:sha(p) for p in out.iterdir() if p.is_file()}}
(out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
