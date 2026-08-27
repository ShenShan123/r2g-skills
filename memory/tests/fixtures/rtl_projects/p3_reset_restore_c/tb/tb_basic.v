`timescale 1ns/1ps
module tb_basic;
  reg clk = 0;
  reg rst_n = 0;
  reg start = 0;
  wire finished;
  reset_restore_c dut(.clk(clk), .rst_n(rst_n), .start(start), .finished(finished));
  always #1 clk = ~clk;
  initial begin
    #2 rst_n = 1;
    #2 start = 1;
    #2;
    if (finished !== 1'b1) $fatal(1, "REGRESSION FAIL: completion lost");
    $display("REGRESSION PASS: completion preserved");
    $finish;
  end
endmodule
