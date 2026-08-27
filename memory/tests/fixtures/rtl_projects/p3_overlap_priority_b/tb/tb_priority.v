module tb_priority;
    reg clk = 0, rst_n = 0, valid = 0, select = 0;
    wire [1:0] state;
    overlap_priority_b dut (.clk(clk), .rst_n(rst_n), .valid(valid), .select(select), .state(state));
    always #5 clk = ~clk;
    initial begin
        #2 rst_n = 1;
        #2 valid = 1; select = 1;
        #12;
        if (state !== 2'd2) $fatal(1, "TARGET FAIL: selected transaction did not win priority");
        $display("TARGET PASS: selected transaction priority preserved");
        $finish;
    end
endmodule
