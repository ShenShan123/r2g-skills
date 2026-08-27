module tb_basic;
    reg clk = 0, rst_n = 0, req = 0, psel = 0;
    wire [1:0] state;
    overlap_priority_a dut (.clk(clk), .rst_n(rst_n), .req(req), .psel(psel), .state(state));
    always #5 clk = ~clk;
    initial begin
        #2 rst_n = 1;
        #2 req = 1; psel = 0;
        #12;
        if (state !== 2'd1) $fatal(1, "REGRESSION FAIL: request service disappeared");
        req = 0; psel = 1;
        #10;
        if (state !== 2'd2) $fatal(1, "REGRESSION FAIL: selected request disappeared");
        $display("REGRESSION PASS: non-overlap arbitration preserved");
        $finish;
    end
endmodule
