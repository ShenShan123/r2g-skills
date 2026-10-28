"""Validate an isolated SV2V reference correction against untouched original RTL.

This never supplies RTL to a physical run. Default parameterization only.
A complete exponent-cone check precedes the full joint equivalence attempt.
"""
from pathlib import Path
import hashlib,json,os,re,resource,subprocess,time

HERE=Path(__file__).resolve().parent
root=HERE/'semantic_evidence/fpu_original_review'
prior=root/'independent_c03963_induction_first_300s'
cone=root/'c03963_aligner_third_frontend'
out=root/'c03963_reviewed_reference_width';out.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
e=json.loads((prior/'result.json').read_text());ce=json.loads((cone/'result.json').read_text())
w=json.loads((HERE/'fpu_package_order_evidence.json').read_text());src=root/'package/sources'/w['repository']
for rel,digest in e['original_source_sha256'].items():assert sha(src/rel.removeprefix('rtl/'))==digest
for n in ('equiv.ys','sv2v.v'):assert sha(prior/n)==e['files'][n]
for n in ('native.v','sv2v_aligner.v'):assert sha(cone/n)==ce['files'][n]
yosys=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys');assert sha(yosys)==e['yosys_sha256']
verilator=Path(ce['verilator_executable']);assert sha(verilator)==ce['verilator_sha256']
text=(prior/'sv2v.v').read_text();m=re.search(r'\bmodule\s+aligner\b.*?endmodule',text,re.S);assert m
old=m[0];assert old.count('function automatic [0:0] sv2v_cast_1;')==1
assert old.count('input reg [0:0] inp;')==1
new=old.replace('function automatic [0:0] sv2v_cast_1;','function automatic [31:0] reviewed_cast_32;').replace('input reg [0:0] inp;','input reg [31:0] inp;').replace('sv2v_cast_1','reviewed_cast_32')
corrected=out/'reference.v';corrected.write_text(text[:m.start()]+new+text[m.end():])
(out/'corrected_aligner.v').write_text(re.sub(r'\bmodule\s+aligner\b','module aligner_sv2v',new,count=1)+'\n')
(out/'negative_aligner.v').write_text((cone/'sv2v_aligner.v').read_text().replace('module aligner_sv2v','module aligner_bad',1))
tb=(cone/'exhaustive_exponent/tb.sv').read_text();decl=re.search(r'aligner_sv2v independent_translation\([^;]+;',tb).group()
tb=tb.replace(decl,decl+'\nwire [9:0] bad_exp;\n'+decl.replace('aligner_sv2v independent_translation','aligner_bad retained_truncation_negative').replace('sv2v_exp','bad_exp'))
tb=tb.replace('if(original_exp !== sv2v_exp) begin','if(original_exp !== sv2v_exp) $fatal(1,"CORRECTED_REFERENCE_MISMATCH");\n if(original_exp !== bad_exp) begin').replace('native_exp,sv2v_exp);','native_exp,bad_exp);')
tb=tb.replace('mismatches==0','mismatches!=9757251');(out/'tb.sv').write_text(tb)
cpu=min(os.sched_getaffinity(0))
def run(cmd,name,limit):
 def limits():
  os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(limit,limit));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
 start=time.monotonic()
 with (out/(name+'.log')).open('w') as log:
  try:rc=subprocess.run(cmd,stdout=log,stderr=log,preexec_fn=limits,timeout=limit+5).returncode
  except subprocess.TimeoutExpired:rc=124
 return dict(returncode=rc,seconds=round(time.monotonic()-start,3))
report=dict(validation_only=True,production_RTL_unchanged=True,physical_execution=False,original_source_sha256=e['original_source_sha256'],original_sv2v_sha256=sha(prior/'sv2v.v'),reviewed_reference_sha256=sha(corrected),correction='Only aligner-local reference cast width 1 to 32, independently validated against original RTL on every 8-bit exponent triple',scope='Default configuration, two-state exponent cone plus full joint proof if completed; not a physical acceptance result',yosys_sha256=sha(yosys),verilator_sha256=sha(verilator),cores=1,memory_gib=4)
def save():
 (out/'progress.json').write_text(json.dumps(report,indent=2)+'\n')
report['build']=run([str(verilator),'--binary','--timing','-j','1','-Wno-fatal','--top-module','tb','--Mdir',str(out/'obj'),str(src/'hdl/fpu_fmac/fpu_defs_fmac.sv'),str(src/'hdl/fpu_fmac/aligner.sv'),str(cone/'native.v'),str(out/'corrected_aligner.v'),str(out/'negative_aligner.v'),str(out/'tb.sv')],'cone_build',90);save()
if report['build']['returncode']==0:
 report['cone_simulation']=run([str(out/'obj/Vtb')],'cone_simulation',90);report['cone_simulation']['log']=(out/'cone_simulation.log').read_text();save()
 if report['cone_simulation']['returncode']==0:
  assert 'PASS original_vs_native=16777216 sv2v_mismatches=9757251' in report['cone_simulation']['log']
  script=(prior/'equiv.ys').read_text().replace(str(prior/'sv2v.v'),str(corrected))
  assert '-blacklist' not in script and script.endswith('equiv_status -assert\n')
  (out/'positive.ys').write_text(script);report['positive']=run([str(yosys),'-s',str(out/'positive.ys')],'positive',300)
  log=(out/'positive.log').read_text();summary=re.search(r'Found (\d+) \$equiv cells.*?(\d+) are proven and (\d+) are unproven',log,re.S)
  report['positive']['equiv_summary']=list(map(int,summary.groups())) if summary else None;save()
  if report['positive']['returncode']==0:
   text=corrected.read_text();start=re.search(r'\bmodule\s+fpu_private\b',text).start();end=text.index('endmodule',start)
   section,n=re.subn(r'(assign\s+result_o\s*=\s*)([^;]+);',lambda m:m[1]+'('+m[2]+") ^ 1'b1;",text[start:end]);assert n==1
   negative=out/'negative.v';negative.write_text(text[:start]+section+text[end:]);(out/'negative.ys').write_text(script.replace(str(corrected),str(negative)))
   report['negative']=run([str(yosys),'-s',str(out/'negative.ys')],'negative',300);report['negative']['mutation']='Actual top result_o XOR 1';save()
report['files']={p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name!='progress.json'}
(out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('files','original_source_sha256')},indent=2))
