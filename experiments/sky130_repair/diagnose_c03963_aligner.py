from pathlib import Path
import json,hashlib,subprocess,os,resource,re,shutil,time
h=Path('/home/yangao/r2g-skills/experiments/sky130_repair');root=h/'semantic_evidence/fpu_original_review';src=root/'package/sources/R02118__pulp-platform__fpu/hdl/fpu_fmac';out=root/'c03963_aligner_third_frontend';out.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();w=json.loads((h/'fpu_package_order_evidence.json').read_text());case=w['cases']['C03963']
for n in ('aligner.sv','fpu_defs_fmac.sv'):assert sha(src/n)==case['source_sha256']['rtl/hdl/fpu_fmac/'+n]
yosys=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys');assert sha(yosys)==w['local_yosys_sha256'];verilator=Path(shutil.which('verilator'));cpu=min(os.sched_getaffinity(0))
def run(cmd,name,limit=90):
 def limits():
  os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(limit,limit));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
 start=time.monotonic()
 with (out/(name+'.log')).open('w') as f:
  try:rc=subprocess.run(cmd,stdout=f,stderr=f,timeout=limit+5,preexec_fn=limits).returncode
  except subprocess.TimeoutExpired:rc=124
 return dict(returncode=rc,seconds=round(time.monotonic()-start,3))
script=out/'native.ys';script.write_text(f'read_verilog -defer -sv {src}/fpu_defs_fmac.sv {src}/aligner.sv\nhierarchy -check -top aligner\nproc\nopt\nrename aligner aligner_native\nwrite_verilog -noattr {out}/native.v\n')
r=run([str(yosys),'-s',str(script)],'native');assert r['returncode']==0
prior=root/'independent_c03963_induction_first_300s';e=json.loads((prior/'result.json').read_text());assert sha(prior/'sv2v.v')==e['files']['sv2v.v'];text=(prior/'sv2v.v').read_text();m=re.search(r'\bmodule\s+aligner\b.*?endmodule',text,re.S);assert m
(out/'sv2v_aligner.v').write_text(re.sub(r'\bmodule\s+aligner\b','module aligner_sv2v',m[0],count=1)+'\n')
inputs='.Exp_a_DI(a),.Exp_b_DI(b),.Exp_c_DI(c),.Mant_a_DI(24\'h800000),.Sign_a_DI(1\'b0),.Sign_b_DI(1\'b0),.Sign_c_DI(1\'b0),.Pp_sum_DI(49\'d0),.Pp_carry_DI(49\'d0),.Sign_change_SI(1\'b0)'
tb='''module tb;
reg [7:0] a,b,c;wire [9:0] original_exp,native_exp,sv2v_exp;
aligner original(INPUTS,.Exp_postalig_DO(original_exp));
aligner_native native_candidate(INPUTS,.Exp_postalig_DO(native_exp));
aligner_sv2v independent_translation(INPUTS,.Exp_postalig_DO(sv2v_exp));
integer vals[9]='{0,1,10,100,127,128,200,254,255};
integer checks=0,mismatches=0;
initial begin
 for(integer ia=0;ia<9;ia++) for(integer ib=0;ib<9;ib++) for(integer ic=0;ic<9;ic++) begin
 a=vals[ia];b=vals[ib];c=vals[ic];#1;
 if(original_exp !== native_exp) $fatal(1,"NATIVE_MISMATCH a=%0d b=%0d c=%0d original=%0d native=%0d",a,b,c,original_exp,native_exp);
 if(original_exp !== sv2v_exp) begin
 if(mismatches<4) $display("SV2V_MISMATCH a=%0d b=%0d c=%0d original=%0d native=%0d sv2v=%0d",a,b,c,original_exp,native_exp,sv2v_exp);
 mismatches++;
 end
 checks++;
 end
 if(checks!=729 || mismatches==0) $fatal(1,"insufficient diagnostic coverage");
 $display("PASS original_vs_native=%0d sv2v_mismatches=%0d",checks,mismatches);$finish;
end
endmodule
'''.replace('INPUTS',inputs);(out/'tb.sv').write_text(tb)
build=run([str(verilator),'--binary','--timing','-j','1','-Wno-fatal','--top-module','tb','--Mdir',str(out/'obj'),str(src/'fpu_defs_fmac.sv'),str(src/'aligner.sv'),str(out/'native.v'),str(out/'sv2v_aligner.v'),str(out/'tb.sv')],'verilator_build')
report=dict(native_compile=r,verilator_build=build,original_source_sha256={n:sha(src/n) for n in ('aligner.sv','fpu_defs_fmac.sv')},yosys_sha256=sha(yosys),verilator_executable=str(verilator),verilator_sha256=sha(verilator),sv2v_source_sha256=sha(prior/'sv2v.v'),scope='Local exponent cone only; original RTL evaluated by independent Verilator; no full-chip proof or physical claim',physical_execution=False)
if build['returncode']==0:report['simulation']=run([str(out/'obj/Vtb')],'simulation');report['simulation']['log']=(out/'simulation.log').read_text()
report['files']={p.name:sha(p) for p in out.iterdir() if p.is_file()};(out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))
