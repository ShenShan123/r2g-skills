`timescale 1ns/1ps
module tb_reset;
  reg clk = 0;
  reg rst_n = 0;
  reg start = 0;
  wire finished;
  reset_restore_c dut(.clk(clk), .rst_n(rst_n), .start(start), .finished(finished));
  always #1 clk = ~clk;
  initial begin
    #2;
    if (finished !== 1'b0) $fatal(1, "TARGET FAIL: reset did not clear finished");
    $display("TARGET PASS: reset semantics preserved");
    $finish;
  end
endmodule
