from pathlib import Path
import shutil,json,hashlib
root=Path(__file__).resolve().parent
q=root.parent
source=Path('/home/yangao/r2g-skills/design_cases/api_repair_cost_verified8_20261028/nangate45/runtime/workspace/10280857-6ec84a/picorv32_axi__10280857-6ec84a/rtl/examples/attosoc/picorv32.v')
shutil.copy2(source,root/'rtl/picorv32.v')
(root/'rtl/picorv32_demo.v').write_text('''// Qualification wrapper: PicoRV32 native memory bus, no external SRAM macro.
module picorv32_demo (
 input clk, resetn,
 output trap, mem_valid, mem_instr,
 input mem_ready,
 output [31:0] mem_addr, mem_wdata,
 output [3:0] mem_wstrb,
 input [31:0] mem_rdata
);
 picorv32 cpu (
  .clk(clk), .resetn(resetn), .trap(trap),
  .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
  .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
  .pcpi_wr(1'b0), .pcpi_rd(32'b0), .pcpi_wait(1'b0), .pcpi_ready(1'b0), .irq(32'b0)
 );
endmodule
''')
config=(q/'gcd_pin_window_v2/constraints/config.mk').read_text().replace('gcd_pin_window_v2','multi_picorv32').replace('export DESIGN_NAME = gcd','export DESIGN_NAME = picorv32_demo').replace('/rtl/gcd.v', '/rtl/picorv32.v '+str(root/'rtl/picorv32_demo.v')).replace('export DIE_AREA = 0 0 16.2 16.2\nexport CORE_AREA = 1.08 1.08 15.12 15.12','export CORE_UTILIZATION = 30\nexport CORE_ASPECT_RATIO = 1\nexport CORE_MARGIN = 1.08')
(root/'constraints/config.mk').write_text(config)
shutil.copy2(q/'gcd_pin_window_v2/constraints/pdn.tcl',root/'constraints/pdn.tcl')
(root/'constraints/constraint.sdc').write_text('current_design picorv32_demo\ncreate_clock -name core_clock -period 1000 [get_ports clk]\nset_input_delay 100 -clock core_clock [all_inputs -no_clocks]\nset_output_delay 100 -clock core_clock [all_outputs]\n')
(root/'metadata.json').write_text(json.dumps(dict(design_name='multi_picorv32',top_module='picorv32_demo',platform='asap7',experimental=True),indent=2)+'\n')
(root/'input/normalized-spec.yaml').write_text('design: picorv32_demo\nplatform: asap7\nclock_port: clk\nclock_period_ps: 1000\nimplementation: Existing PicoRV32, unmodified default parameters, native memory interface\n')
(root/'input/source_provenance.json').write_text(json.dumps(dict(source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),copied_unmodified=True,license='ISC notice preserved in RTL',top='picorv32_demo',parameters='All PicoRV32 defaults: RV32I, 32 registers, dual-port register file, 64-bit counters; no C/M, IRQ, or PCPI; reset PC 0',external_sram_macros=0),indent=2)+'\n')
