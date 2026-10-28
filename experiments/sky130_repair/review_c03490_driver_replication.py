#!/usr/bin/env python3
"""Exhaust the actual inout drive cone, including independent external drivers.

This is a local combinational cone proof, not full-chip or timing signoff.
The rejected control buffers a resolved inout; the candidate replicates the
original inverter from its input, keeping every external pin independent.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess

HERE=Path(__file__).resolve().parent


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    root=Path('/tmp/sky130_aux208');src=root/'package/sources/R01908__thejpster__zube/src/zube_wrapper.v'
    diagnosis=json.loads((HERE/'semantic_evidence/c03490_bidirectional_diagnosis.json').read_text())
    for rel,digest in diagnosis['evidence'].items():
        if sha(root/rel)!=digest:raise ValueError('original diagnosed source or mapped evidence changed')
    rtl=src.read_text();statement="assign io_oeb[15:8] = io_out[16] ? 8'h00 : 8'hFF;"
    if rtl.count(statement)!=1:raise ValueError('unexpected original drive expression')
    # io_oeb is never read internally: two macro alternatives for its declaration
    # and three independently assigned slices are the only non-comment uses.
    code=re.sub(r'/\*.*?\*/|//[^\n]*','',rtl,flags=re.S)
    uses=[line.strip() for line in code.splitlines() if re.search(r'\bio_oeb\b',line)]
    if len(uses)!=5 or any('=' in line and 'io_oeb' in line.split('=',1)[1] for line in uses):raise ValueError('unreviewed output-enable feedback')
    models=root/'environment/miniconda3/envs/eda/share/pdk/sky130A/libs.ref/sky130_fd_sc_hd/verilog'
    out=HERE/'semantic_evidence/c03490_driver_replication';out.mkdir(exist_ok=False)
    shutil.copy2(src,out/src.name)
    # Actual PDK functional models, with normal supplies; no abstract buffer
    # replacing the independent candidate or the deliberately invalid control.
    tb='''`timescale 1ns/1ps
module tb;
 reg control;
 reg [7:0] external_drive;
 tri [7:0] original_pin, candidate_pin, bad_pin;
 assign original_pin = external_drive;
 assign candidate_pin = external_drive;
 assign bad_pin = external_drive;
 ORIGINAL_STATEMENT
 genvar g;
 generate for(g=0;g<8;g=g+1) begin: candidate
   sky130_fd_sc_hd__inv_1 inv(.A(control),.Y(candidate_pin[g]),.VPWR(1'b1),.VGND(1'b0),.VPB(1'b1),.VNB(1'b0));
 end endgenerate
 sky130_fd_sc_hd__inv_1 bad_root(.A(control),.Y(bad_pin[0]),.VPWR(1'b1),.VGND(1'b0),.VPB(1'b1),.VNB(1'b0));
 generate for(g=1;g<8;g=g+1) begin: bad
   sky130_fd_sc_hd__buf_4 bufcell(.A(bad_pin[0]),.X(bad_pin[g]),.VPWR(1'b1),.VGND(1'b0),.VPB(1'b1),.VNB(1'b0));
 end endgenerate
 function state4(input integer s);
  case(s) 0:state4=1'b0;1:state4=1'b1;2:state4=1'bx;3:state4=1'bz;endcase
 endfunction
 integer c,pattern,bit_index,checks,negative_mismatches;
 initial begin
  checks=0;negative_mismatches=0;
  for(c=0;c<4;c=c+1) begin
   control=state4(c);
   for(pattern=0;pattern<65536;pattern=pattern+1) begin
    for(bit_index=0;bit_index<8;bit_index=bit_index+1)
      external_drive[bit_index]=state4((pattern>>(2*bit_index))&3);
    #1;
    if(original_pin !== candidate_pin) $fatal(1,"candidate mismatch control=%b external=%b original=%b candidate=%b",control,external_drive,original_pin,candidate_pin);
    if(original_pin !== bad_pin) begin
     if(negative_mismatches==0) $display("NEGATIVE control=%b external=%b original=%b buffered=%b",control,external_drive,original_pin,bad_pin);
     negative_mismatches=negative_mismatches+1;
    end
    checks=checks+1;
   end
  end
  if(checks!=262144 || negative_mismatches==0) $fatal(1,"vacuous or incomplete test");
  $display("PASS checks=%0d negative_mismatches=%0d",checks,negative_mismatches);
  $finish;
 end
endmodule
'''.replace('ORIGINAL_STATEMENT',statement.replace('io_oeb[15:8]','original_pin').replace('io_out[16]','control'))
    (out/'tb.v').write_text(tb);cpu=min(os.sched_getaffinity(0))
    def limits():
        os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(45,45));resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    with (out/'compile.log').open('w') as log:
        subprocess.run(['iverilog','-g2012','-DFUNCTIONAL','-DUSE_POWER_PINS','-DUNIT_DELAY=','-s','tb','-o',str(out/'sim.vvp'),str(out/'tb.v'),str(models/'primitives.v'),str(models/'sky130_fd_sc_hd.v')],stdout=log,stderr=log,check=True,timeout=50,preexec_fn=limits)
    with (out/'simulation.log').open('w') as log:
        result=subprocess.run(['vvp',str(out/'sim.vvp')],stdout=log,stderr=log,timeout=50,preexec_fn=limits)
    log=(out/'simulation.log').read_text();match=re.search(r'PASS checks=(\d+) negative_mismatches=(\d+)',log)
    passed=result.returncode==0 and match and int(match[1])==262144 and int(match[2])>0
    report=dict(design=diagnosis['design'],source_sha256=sha(src),original_statement=statement,output_enable_uses=uses,
                interface_unchanged=True,defines_changed=False,local_cone_exhaustive=bool(passed),states_per_signal=4,
                simulation_model_defines=['FUNCTIONAL','USE_POWER_PINS','UNIT_DELAY='],original_design_macros_changed=False,
                checks=int(match[1]) if match else None,negative_mismatches=int(match[2]) if match else None,
                candidate='eight independent original inverters driven by resolved io_out[16]; never read another io_oeb pin',
                negative='one inverter followed by buffers from resolved io_oeb[8]',
                scope='Exhaustive zero-delay four-state local cone and independent strong external drivers; not full-chip equivalence or physical signoff',
                model_sha256={p.name:sha(p) for p in (models/'primitives.v',models/'sky130_fd_sc_hd.v')},
                files={p.name:sha(p) for p in out.iterdir() if p.is_file()},physical_signoff=False)
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('files','output_enable_uses')}))
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
