from pathlib import Path
import subprocess,os,resource,json,hashlib,time
h=Path(__file__).resolve().parent;o=h/'semantic_evidence/c03878_cts_review';orfs=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts');exe=orfs/'tools/install/OpenROAD/bin/openroad';script=o/'export_cts.tcl'
script.write_text(f'''read_liberty {orfs}/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
read_db {o}/3_place.odb
read_sdc {o}/constraint.sdc
source {orfs}/flow/platforms/sky130hd/setRC.tcl
set_thread_count 1
write_verilog {o}/before_cts.v
puts BEFORE_REPAIR_CLOCK_INVERTERS
flush stdout
puts DIAGNOSTIC_ONLY_INVERTER_CLONE_OMITTED
puts AFTER_REPAIR_CLOCK_INVERTERS
flush stdout
clock_tree_synthesis -no_insertion_delay -sink_clustering_enable -repair_clock_nets -buf_list sky130_fd_sc_hd__clkbuf_1
write_verilog {o}/after_cts.v
write_db {o}/after_cts.odb
puts AFTER_CTS
exit
''')
def limits():
 os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});resource.setrlimit(resource.RLIMIT_CPU,(30,30));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
start=time.time()
with (o/'export_cts.log').open('w') as f:
 try:r=subprocess.run([str(exe),'-exit',str(script)],stdout=f,stderr=f,preexec_fn=limits,timeout=40);rc=r.returncode
 except subprocess.TimeoutExpired:rc='wall_timeout_40s'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
report={'returncode':rc,'seconds':round(time.time()-start,2),'cores':1,'cpu_seconds':30,'memory_gib':4,'log':(o/'export_cts.log').read_text(),'original_files':{n:sha(o/n) for n in ['3_place.odb','constraint.sdc','D_ff.v','NAND_gate.v','config.mk','result.json']},'openroad_sha256':sha(exe),'scope':'Original placed DB and SDC, bounded diagnostic, no semantic rewrite or physical acceptance'}
(o/'export_cts_result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='log'}))
