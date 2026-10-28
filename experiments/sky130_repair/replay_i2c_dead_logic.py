from pathlib import Path
import subprocess,json,hashlib,resource,os,re
h=Path('/home/yangao/r2g-skills/experiments/sky130_repair');root=h/'semantic_evidence/c03259_tristate_review';out=root/'dead_logic_preservation/traffic';out.mkdir(exist_ok=False);prior=root/'hierarchical_probe/traffic_after_frozen_synthesis';models=Path('/tmp/sky130_aux208/environment/miniconda3/envs/eda/share/pdk/sky130A/libs.ref/sky130_fd_sc_hd/verilog');cpu=min(os.sched_getaffinity(0));sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def limits():
 os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(90,90));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
results={}
for role,file in [('positive','preserved.v'),('negative','control.v')]:
 source=out/(role+'.v');text=(out.parent/file).read_text();text,n=re.subn(r'\bmodule\s+I2C_module\b','module gate_I2C_module',text,count=1);assert n==1;source.write_text(text)
 with (out/(role+'_compile.log')).open('w') as f:r=subprocess.run(['iverilog','-g2012','-DFUNCTIONAL','-DUNIT_DELAY=','-s','tb','-o',str(out/(role+'.vvp')),str(prior/'tb.v'),str(prior/'original_top.v'),str(root/'rtl/I2C_master.v'),str(root/'rtl/I2C_slave.v'),str(source),str(models/'primitives.v'),str(models/'sky130_fd_sc_hd.v')],stdout=f,stderr=f,timeout=95,preexec_fn=limits)
 assert r.returncode==0,role
 with (out/(role+'.log')).open('w') as f:r=subprocess.run(['vvp',str(out/(role+'.vvp'))],stdout=f,stderr=f,timeout=95,preexec_fn=limits)
 results[role]=dict(returncode=r.returncode,log=(out/(role+'.log')).read_text(),mapped_sha256=sha(out.parent/file));print(role,results[role]['log'])
report=dict(results=results,testbench_sha256=sha(prior/'tb.v'),original_source_sha256={n:sha(root/'rtl'/n) for n in ('I2C_module.v','I2C_master.v','I2C_slave.v')},physical_signoff=False,scope='Original bounded four-state traffic versus actual post-eliminate_dead_logic mapped netlist; control keeps the real destructive cleanup result')
(out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
