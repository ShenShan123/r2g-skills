module tb_basic;
    reg clk = 0, rst_n = 0, request = 0, grant_req = 0;
    wire [1:0] state;
    overlap_priority_c dut (.clk(clk), .rst_n(rst_n), .request(request),
                            .grant_req(grant_req), .state(state));
    always #5 clk = ~clk;
    initial begin
        #2 rst_n = 1;
        #2 request = 1; grant_req = 0;
        #12;
        if (state !== 2'd1) $fatal(1, "REGRESSION FAIL: active path disappeared");
        request = 0; grant_req = 1;
        #10;
        if (state !== 2'd2) $fatal(1, "REGRESSION FAIL: retry path disappeared");
        $display("REGRESSION PASS: non-overlap arbitration preserved");
        $finish;
    end
endmodule
