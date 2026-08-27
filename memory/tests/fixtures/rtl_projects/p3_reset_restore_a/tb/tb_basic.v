module tb_basic;
    reg clk = 0, rst_n = 1, start = 0;
    wire done;
    reset_restore_a dut (.clk(clk), .rst_n(rst_n), .start(start), .done(done));
    always #5 clk = ~clk;
    initial begin
        #4 rst_n = 0;
        #4 rst_n = 1;
        #8 start = 1;
        #12;
        if (done !== 1'b1) $fatal(1, "REGRESSION FAIL: completion missing");
        $display("REGRESSION PASS: completion preserved");
        $finish;
    end
endmodule
