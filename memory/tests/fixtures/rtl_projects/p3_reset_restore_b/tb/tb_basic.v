module tb_basic;
    reg clk = 0, rst_n = 1, launch = 0;
    wire complete;
    reset_restore_b dut (.clk(clk), .rst_n(rst_n), .launch(launch), .complete(complete));
    always #5 clk = ~clk;
    initial begin
        #4 rst_n = 0;
        #4 rst_n = 1;
        #8 launch = 1;
        #12;
        if (complete !== 1'b1) $fatal(1, "REGRESSION FAIL: completion missing");
        $display("REGRESSION PASS: completion preserved");
        $finish;
    end
endmodule
