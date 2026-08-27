module tb_priority;
    reg clk = 0, rst_n = 0, request = 0, grant_req = 0;
    wire [1:0] state;
    overlap_priority_c dut (.clk(clk), .rst_n(rst_n), .request(request),
                            .grant_req(grant_req), .state(state));
    always #5 clk = ~clk;
    initial begin
        #2 rst_n = 1;
        #2 request = 1; grant_req = 1;
        #12;
        if (state !== 2'd2) $fatal(1, "TARGET FAIL: grant request did not win priority");
        $display("TARGET PASS: grant request priority preserved");
        $finish;
    end
endmodule
