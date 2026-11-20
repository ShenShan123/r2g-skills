`timescale 1ns/1ps
module picorv32_tb;
 reg clk=0; always #0.5 clk=~clk;
 reg resetn=0;
 wire trap,mem_valid,mem_instr;
 reg mem_ready=0;
 wire [31:0] mem_addr,mem_wdata;
 wire [3:0] mem_wstrb;
 reg [31:0] mem_rdata=0;
 reg [31:0] memory[0:1023];
 integer cycle=0,mode,completed=0,k,b,reads,writes;
 picorv32_demo dut(.*);
 always @(posedge clk) begin
  cycle<=cycle+1;
  if(resetn && mem_valid && mem_ready) begin
   if(mem_addr>=4096 || mem_addr[1:0]!=0) $fatal(1,"Invalid bus address %h",mem_addr);
   if(mem_wstrb!=0) begin
    for(b=0;b<4;b=b+1) if(mem_wstrb[b]) memory[mem_addr>>2][b*8+:8]<=mem_wdata[b*8+:8];
    writes<=writes+1;
   end else reads<=reads+1;
  end
 end
 always @(negedge clk) begin
  mem_ready=resetn && mem_valid && (mode==0 || (cycle%(mode+2))==0);
  mem_rdata=mem_valid && mem_addr<4096 ? memory[mem_addr>>2] : 0;
 end
 task check_word(input integer address,input [31:0] expected);
  begin if(memory[address>>2]!==expected) $fatal(1,"signature address=%h actual=%h expected=%h",address,memory[address>>2],expected); end
 endtask
 initial begin
  for(mode=0;mode<3;mode=mode+1) begin
   resetn=0; reads=0; writes=0;
   for(k=0;k<1024;k=k+1) memory[k]=0;
   $readmemh("tb/program.hex",memory,0,39);
   repeat(8) @(negedge clk);
   resetn=1;
   wait(trap);
   @(negedge clk);
   check_word(256,55); check_word(260,55); check_word(264,32'h0fffffff); check_word(268,1);
   check_word(272,255); check_word(276,32'hffffffff); check_word(280,255); check_word(284,42); check_word(288,32'h60d);
   if(writes!=9 || reads<90) $fatal(1,"Bad bus activity reads=%0d writes=%0d",reads,writes);
   $display("PASS mode=%0d cycles=%0d reads=%0d writes=%0d signatures=9",mode,cycle,reads,writes);
   completed=completed+1;
  end
  $display("PASS all %0d memory-latency modes: loops branches arithmetic shifts signed/unsigned byte loads stores JAL/JALR EBREAK",completed);
  $finish;
 end
 initial begin #30000; $fatal(1,"timeout"); end
endmodule
