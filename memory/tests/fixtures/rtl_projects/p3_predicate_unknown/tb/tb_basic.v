module tb_basic;
    reg clk = 0, rst_n = 0, valid = 0, ready = 0;
    wire accepted;
    integer cycles = 0, accepted_high_cycles = 0;
    predicate_unknown_fsm dut (.clk(clk), .rst_n(rst_n), .valid(valid), .ready(ready), .accepted(accepted));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        cycles = cycles + 1;
        if (accepted) accepted_high_cycles = accepted_high_cycles + 1;
        if (cycles > 80) begin
            if (accepted_high_cycles == 0) $fatal(1, "REGRESSION FAIL: completion disappeared");
            $display("REGRESSION PASS: completion reached");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 valid = 1; #40 ready = 1; #20 ready = 0; #20 valid = 0;
    end
endmodule
