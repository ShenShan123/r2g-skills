module tb_reset;
    reg clk = 0, rst_n = 1, launch = 0;
    wire complete;
    reset_restore_b dut (.clk(clk), .rst_n(rst_n), .launch(launch), .complete(complete));
    always #5 clk = ~clk;
    initial begin
        #2 rst_n = 0;
        #2;
        if (complete !== 1'b0) $fatal(1, "TARGET FAIL: reset did not clear complete");
        rst_n = 1;
        #8 launch = 1;
        #10;
        if (complete !== 1'b1) $fatal(1, "TARGET FAIL: launch did not assert complete");
        $display("TARGET PASS: reset semantics preserved");
        $finish;
    end
endmodule
