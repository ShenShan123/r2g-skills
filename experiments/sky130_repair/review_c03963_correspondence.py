from pathlib import Path
import hashlib,json,os,re,resource,subprocess,time
h=Path('/home/yangao/r2g-skills/experiments/sky130_repair');root=h/'semantic_evidence/fpu_original_review';prior=root/'independent_c03963_induction_first_300s';out=root/'c03963_internal_correspondence_review';out.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();e=json.loads((prior/'result.json').read_text());w=json.loads((h/'fpu_package_order_evidence.json').read_text());sr=root/'package/sources'/w['repository']
for rel,digest in e['original_source_sha256'].items():assert sha(sr/rel.removeprefix('rtl/'))==digest
for n in ('equiv.ys','equiv.log','sv2v.v'):assert sha(prior/n)==e['files'][n]
yosys=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys');assert sha(yosys)==e['yosys_sha256']
expected={'fp_fma_wrap_i.fp_fma_i.Exp_postalig_D','fp_fma_wrap_i.fp_fma_i.aligner_U0.Exp_postalig_DO','fp_fma_wrap_i.fp_fma_i.fpu_norm_U0.Exp_in_DI'}
names=set(re.findall(r'Unproven \$equiv .*?: \\(\S+)_gold \[\d+\]',(prior/'equiv.log').read_text()));assert names==expected
blacklist=out/'internal_names.txt';blacklist.write_text('\n'.join(sorted(names))+'\n')
script=(prior/'equiv.ys').read_text().replace('equiv_make gold gate equiv',f'equiv_make -blacklist {blacklist} gold gate equiv').replace('rename fpu_private gold',f'write_json {out}/gold.json\nrename fpu_private gold').replace('rename fpu_private gate',f'write_json {out}/gate.json\nrename fpu_private gate')
# This only removes three proposed internal correspondence points. It does not
# remove their logic, tie their values, assume equality or exclude top outputs.
(out/'positive.ys').write_text(script);cpu=min(os.sched_getaffinity(0))
def run(role):
 def limits():
  os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(300,300));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
 start=time.monotonic()
 with (out/(role+'.log')).open('w') as f:
  try:code=subprocess.run([str(yosys),'-s',str(out/(role+'.ys'))],stdout=f,stderr=f,timeout=305,preexec_fn=limits).returncode
  except subprocess.TimeoutExpired:code=124
 log=(out/(role+'.log')).read_text();m=re.search(r'Found (\d+) \$equiv cells.*?(\d+) are proven and (\d+) are unproven',log,re.S)
 return dict(returncode=code,seconds=round(time.monotonic()-start,3),equiv_summary=list(map(int,m.groups())) if m else None)
positive=run('positive');modules=[json.loads((out/(r+'.json')).read_text())['modules']['fpu_private'] for r in ('gold','gate')]
ports=[{n:{'direction':p['direction'],'width':len(p['bits'])} for n,p in m['ports'].items()} for m in modules];assert ports[0]==ports[1]
for m in modules:
 assert not (names & set(m['ports'])) and names<=set(m['netnames'])
report=dict(positive=positive,matched_top_interface=ports[0],all_top_ports_retained=True,internal_correspondence_names=sorted(names),logic_unchanged=True,no_new_assumptions=True,prior_evidence_sha256=sha(prior/'result.json'),original_source_sha256=e['original_source_sha256'],yosys_sha256=sha(yosys),cores=1,native_memory_gib=4,cpu_seconds_limit=300,physical_signoff=False)
if positive['returncode']==0:
 text=(prior/'sv2v.v').read_text();start=re.search(r'\bmodule\s+fpu_private\b',text).start();end=text.index('endmodule',start);section=text[start:end]
 mutant,n=re.subn(r'(assign\s+result_o\s*=\s*)([^;]+);',lambda m:m[1]+'('+m[2]+") ^ 1'b1;",section);assert n==1
 negative=out/'negative.v';negative.write_text(text[:start]+mutant+text[end:]);(out/'negative.ys').write_text(script.replace(str(prior/'sv2v.v'),str(negative)).replace(f'write_json {out}/gold.json\n','').replace(f'write_json {out}/gate.json\n',''))
 report['negative']=run('negative');report['negative']['mutation']='Actual top result_o XOR 1'
report['files']={p.name:sha(p) for p in out.iterdir() if p.is_file()};(out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('files','original_source_sha256','matched_top_interface')},indent=2))
