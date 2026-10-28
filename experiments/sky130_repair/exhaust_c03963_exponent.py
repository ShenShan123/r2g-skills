from pathlib import Path
import json,hashlib,re,os,resource,subprocess,time
h=Path('/home/yangao/r2g-skills/experiments/sky130_repair');root=h/'semantic_evidence/fpu_original_review';prior=root/'c03963_aligner_third_frontend';out=prior/'exhaustive_exponent';out.mkdir(exist_ok=False);src=root/'package/sources/R02118__pulp-platform__fpu/hdl/fpu_fmac';e=json.loads((prior/'result.json').read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for name,digest in e['original_source_sha256'].items():assert sha(src/name)==digest
for name in ('native.v','sv2v_aligner.v','tb.sv'):assert sha(prior/name)==e['files'][name]
verilator=Path(e['verilator_executable']);assert sha(verilator)==e['verilator_sha256'];tb=(prior/'tb.sv').read_text();tb=re.sub(r"integer vals\[9\]='\{[^;]+;\n",'',tb);tb=tb.replace('<9;','<256;').replace('a=vals[ia];b=vals[ib];c=vals[ic];','a=ia;b=ib;c=ic;').replace('checks!=729','checks!=16777216');assert 'vals' not in tb;assert tb.count('<256;')==3;(out/'tb.sv').write_text(tb);cpu=min(os.sched_getaffinity(0))
def run(cmd,name):
 def limits():
  os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(90,90));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
 start=time.monotonic()
 with (out/(name+'.log')).open('w') as f:
  try:rc=subprocess.run(cmd,stdout=f,stderr=f,timeout=95,preexec_fn=limits).returncode
  except subprocess.TimeoutExpired:rc=124
 return dict(returncode=rc,seconds=round(time.monotonic()-start,3))
report=dict(scope='Exhaustive three 8-bit exponents for original aligner exponent output only; no full-chip/timing claim',original_source_sha256=e['original_source_sha256'],original_native_and_sv2v_artifact_sha256={n:sha(prior/n) for n in ('native.v','sv2v_aligner.v')},verilator_sha256=sha(verilator),physical_execution=False,cores=1,memory_gib=4)
report['build']=run([str(verilator),'--binary','--timing','-j','1','-Wno-fatal','--top-module','tb','--Mdir',str(out/'obj'),str(src/'fpu_defs_fmac.sv'),str(src/'aligner.sv'),str(prior/'native.v'),str(prior/'sv2v_aligner.v'),str(out/'tb.sv')],'build')
if report['build']['returncode']==0:report['simulation']=run([str(out/'obj/Vtb')],'simulation');report['simulation']['log']=(out/'simulation.log').read_text()
report['files']={p.name:sha(p) for p in out.iterdir() if p.is_file()};(out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))
