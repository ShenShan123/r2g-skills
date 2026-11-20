// Qualification wrapper: PicoRV32 native memory bus, no external SRAM macro.
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
