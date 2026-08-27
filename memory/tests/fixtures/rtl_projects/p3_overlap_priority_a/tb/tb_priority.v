module tb_priority;
    reg clk = 0, rst_n = 0, req = 0, psel = 0;
    wire [1:0] state;
    overlap_priority_a dut (.clk(clk), .rst_n(rst_n), .req(req), .psel(psel), .state(state));
    always #5 clk = ~clk;
    initial begin
        #2 rst_n = 1;
        #2 req = 1; psel = 1;
        #12;
        if (state !== 2'd2) $fatal(1, "TARGET FAIL: selected request did not win priority");
        $display("TARGET PASS: selected request priority preserved");
        $finish;
    end
endmodule
