module tb_reset;
    reg clk = 0, rst_n = 1, start = 0;
    wire done;
    reset_restore_a dut (.clk(clk), .rst_n(rst_n), .start(start), .done(done));
    always #5 clk = ~clk;
    initial begin
        #2 rst_n = 0;
        #2;
        if (done !== 1'b0) $fatal(1, "TARGET FAIL: reset did not clear done");
        rst_n = 1;
        #8 start = 1;
        #10;
        if (done !== 1'b1) $fatal(1, "TARGET FAIL: start did not assert done");
        $display("TARGET PASS: reset semantics preserved");
        $finish;
    end
endmodule
