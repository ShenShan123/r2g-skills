#!/usr/bin/env python3
"""Compare the original I2C RTL with the real-cell hierarchical candidate.

Bounded four-state output comparison with a board pull-up and independent
external SDA drive. Not an unbounded proof or a physical/signoff result.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess

HERE=Path(__file__).resolve().parent


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--frozen-synthesis',action='store_true');args=parser.parse_args()
    root=HERE/'semantic_evidence/c03259_tristate_review';probe=root/'hierarchical_probe'
    out=probe/('traffic_after_frozen_synthesis' if args.frozen_synthesis else 'traffic');out.mkdir(exist_ok=False)
    diagnosis=json.loads((root/'diagnosis.json').read_text());compiled=json.loads((probe/'result.json').read_text())
    for name in ('I2C_module.v','I2C_master.v','I2C_slave.v'):
        if sha(root/'rtl'/name)!=diagnosis['sha256']['rtl/'+name]:raise ValueError('original RTL changed')
    if sha(probe/'mapped.v')!=compiled['files']['mapped.v']:raise ValueError('candidate differs from checked mapping')
    mapped=probe/'mapped.v'
    if args.frozen_synthesis:
        frozen=json.loads((probe/'frozen_synthesis_probe.json').read_text())
        mapped=Path(frozen['work'])/'results/sky130hd/I2C_module/base/1_2_yosys.v'
        if frozen['returncode']!=0 or sha(mapped)!=frozen['frozen_flow_synthesized_sha256']:
            raise ValueError('frozen synthesis output differs from the actual compiler probe')
    original=(root/'rtl/I2C_module.v').read_bytes();assert original.count(b'module I2C_module')==1
    (out/'original_top.v').write_bytes(original.replace(b'module I2C_module',b'module gold_I2C_module',1))
    candidate=mapped.read_text();assert candidate.count('module I2C_module(')==1
    candidate=candidate.replace('module I2C_module(','module gate_I2C_module(',1)
    (out/'mapped_top.v').write_text(candidate)
    negative,n=re.subn(r'\.TE_B\([^)]*\)',".TE_B(1'b1)",candidate,count=1);assert n==1
    (out/'negative_top.v').write_text(negative)
    ports='''.clk(clk),.rst(rst),.Master_data_in(md),.START(start),.RD_WR(rw),.Slave_Address(addr),.Slave_data_in(sd)'''
    tb='''`timescale 1ns/1ps
module tb;
 reg clk=0,rst=1,start=0,rw=0;
 reg [7:0] md=0,sd=0;
 reg [6:0] addr=0;
 reg external_drive=1'bz;
 tri gold_sda,gate_sda;
 pullup(gold_sda);pullup(gate_sda);
 assign gold_sda=external_drive;assign gate_sda=external_drive;
 wire [7:0] gold_md,gate_md,gold_sd,gate_sd;
 wire gold_done,gate_done,gold_sdone,gate_sdone,gold_scl,gate_scl;
 wire [19:0] golden={gold_md,gold_done,gold_sd,gold_sdone,gold_scl,gold_sda};
 wire [19:0] actual={gate_md,gate_done,gate_sd,gate_sdone,gate_scl,gate_sda};
 gold_I2C_module original(PORTS,.sda(gold_sda),.scl(gold_scl),.Master_dataout(gold_md),.Master_done(gold_done),.Slave_dataout(gold_sd),.Slave_done(gold_sdone));
 gate_I2C_module candidate(PORTS,.sda(gate_sda),.scl(gate_scl),.Master_dataout(gate_md),.Master_done(gate_done),.Slave_dataout(gate_sd),.Slave_done(gate_sdone));
 integer checks=0,known=0,transitions=0,master_done_samples=0,slave_done_samples=0;
 integer frame,cycle;reg prior_sda=1'bx;
 task compare;
 begin
  if(golden !== actual) $fatal(1,"MISMATCH frame=%0d cycle=%0d time=%0t rst=%b external=%b gold=%b actual=%b",frame,cycle,$time,rst,external_drive,golden,actual);
  checks=checks+1;if((^golden)!==1'bx) known=known+1;
  if(prior_sda!==gold_sda) transitions=transitions+1;prior_sda=gold_sda;
  if(gold_done===1'b1) master_done_samples=master_done_samples+1;
  if(gold_sdone===1'b1) slave_done_samples=slave_done_samples+1;
 end endtask
 task tick;
 begin clk=0;#2;clk=1;#2;compare;clk=0;#1;end
 endtask
 initial begin
  for(frame=0;frame<64;frame=frame+1) begin
   external_drive=1'bz;start=0;rst=0;#2;
   tick;tick;rst=1;md=(frame*73)^8'ha5;sd=(frame*29)^8'h3c;addr=(frame*17)&127;rw=frame&1;
   for(cycle=0;cycle<1024;cycle=cycle+1) begin
    start=(cycle==4 || cycle==512);
    external_drive=1'bz;
    if(frame>=32 && cycle>=80 && cycle<84) external_drive=frame&1;
    tick;
   end
  end
  if(checks<65000 || known<1000 || transitions<100) $fatal(1,"insufficient observable traffic");
  $display("PASS checks=%0d known=%0d sda_transitions=%0d master_done_samples=%0d slave_done_samples=%0d",checks,known,transitions,master_done_samples,slave_done_samples);
  $finish;
 end
endmodule
'''.replace('PORTS',ports)
    (out/'tb.v').write_text(tb)
    models=Path('/tmp/sky130_aux208/environment/miniconda3/envs/eda/share/pdk/sky130A/libs.ref/sky130_fd_sc_hd/verilog')
    cpu=min(os.sched_getaffinity(0))
    def limits():
        os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(90,90));resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
    results={}
    for role,source in [('positive','mapped_top.v'),('negative','negative_top.v')]:
        if role=='negative' and results['positive']['returncode']!=0:break
        executable=out/(role+'.vvp')
        with (out/(role+'_compile.log')).open('w') as log:
            subprocess.run(['iverilog','-g2012','-DFUNCTIONAL','-DUNIT_DELAY=','-s','tb','-o',str(executable),str(out/'tb.v'),str(out/'original_top.v'),str(root/'rtl/I2C_master.v'),str(root/'rtl/I2C_slave.v'),str(out/source),str(models/'primitives.v'),str(models/'sky130_fd_sc_hd.v')],stdout=log,stderr=log,check=True,timeout=40,preexec_fn=limits)
        with (out/(role+'.log')).open('w') as log:
            try:code=subprocess.run(['vvp',str(executable)],stdout=log,stderr=log,preexec_fn=limits,timeout=95).returncode
            except subprocess.TimeoutExpired:code=124
        results[role]=dict(returncode=code,log_tail=(out/(role+'.log')).read_text()[-1200:])
    report=dict(results=results,original_source_sha256={n:diagnosis['sha256']['rtl/'+n] for n in ('I2C_module.v','I2C_master.v','I2C_slave.v')},mapped_sha256=sha(mapped),after_frozen_synthesis=args.frozen_synthesis,
                environment='Shared digital inputs, actual independent SDA buses with weak pull-ups, released or 0/1 external drive; synchronous traffic and asynchronous reset',
                frames=64,cycles_per_frame=1024,compared_top_bits=20,unbounded_equivalence=False,physical_signoff=False,
                files={p.name:sha(p) for p in out.iterdir() if p.is_file()})
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='files'}))


if __name__=='__main__':main()
