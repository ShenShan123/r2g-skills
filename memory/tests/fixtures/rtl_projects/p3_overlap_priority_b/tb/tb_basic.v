module tb_basic;
    reg clk = 0, rst_n = 0, valid = 0, select = 0;
    wire [1:0] state;
    overlap_priority_b dut (.clk(clk), .rst_n(rst_n), .valid(valid), .select(select), .state(state));
    always #5 clk = ~clk;
    initial begin
        #2 rst_n = 1;
        #2 valid = 1; select = 0;
        #12;
        if (state !== 2'd1) $fatal(1, "REGRESSION FAIL: serve path disappeared");
        valid = 0; select = 1;
        #10;
        if (state !== 2'd2) $fatal(1, "REGRESSION FAIL: retry path disappeared");
        $display("REGRESSION PASS: non-overlap arbitration preserved");
        $finish;
    end
endmodule
