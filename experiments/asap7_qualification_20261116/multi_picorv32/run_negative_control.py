"""Ensure the final-netlist program test rejects a deliberate output error."""
from pathlib import Path
import subprocess,json,hashlib
p=Path(__file__).resolve().parent
net=sorted((p/'backend').glob('RUN_*/results/6_final.v'))[-1]
s=net.read_text().replace('module picorv32_demo (','module picorv32_demo_original (',1)
assert s!=net.read_text()
s+='''\nmodule picorv32_demo(input clk,resetn,mem_ready,input [31:0] mem_rdata,
output trap,mem_valid,mem_instr,output [31:0] mem_addr,mem_wdata,output [3:0] mem_wstrb);
wire [31:0] original_wdata;
picorv32_demo_original core(.clk(clk),.resetn(resetn),.mem_ready(mem_ready),.mem_rdata(mem_rdata),.trap(trap),.mem_valid(mem_valid),.mem_instr(mem_instr),.mem_addr(mem_addr),.mem_wdata(original_wdata),.mem_wstrb(mem_wstrb));
assign mem_wdata=original_wdata ^ 32'b1;
endmodule
'''
mut=p/'tb/negative_wdata_bit0.v';mut.write_text(s)
commands=[['iverilog','-g2012','-s','picorv32_tb','-o','reports/negative_sim','tb/negative_wdata_bit0.v','tb/cells_from_liberty.v','tb/picorv32_tb.v'],['vvp','reports/negative_sim']]
with (p/'reports/negative_compile.log').open('w') as f:
 r=subprocess.run(commands[0],cwd=p,stdout=f,stderr=subprocess.STDOUT,check=True)
with (p/'reports/negative_sim.log').open('w') as f:
 r=subprocess.run(commands[1],cwd=p,stdout=f,stderr=subprocess.STDOUT)
log=(p/'reports/negative_sim.log').read_text()
assert r.returncode!=0 and 'signature address=' in log,log
(p/'reports/negative_control.json').write_text(json.dumps({'result':'PASS: deliberate mutation rejected','mutation':'Invert final netlist mem_wdata[0] in a separate wrapper; original files unchanged.','sim_exit':r.returncode,'commands':commands,'original_netlist_sha256':hashlib.sha256(net.read_bytes()).hexdigest(),'mutant_sha256':hashlib.sha256(mut.read_bytes()).hexdigest(),'failure':log},indent=2)+'\n')
print(log)
