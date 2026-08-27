module tb_handshake;
    reg clk = 0, rst_n = 0, valid = 0, ready = 0;
    wire accepted;
    integer violations = 0, cycles = 0;
    predicate_unknown_fsm dut (.clk(clk), .rst_n(rst_n), .valid(valid), .ready(ready), .accepted(accepted));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        if (dut.state == 2'd1 && dut.next_state == 2'd2 && !ready)
            violations = violations + 1;
        cycles = cycles + 1;
        if (cycles > 80) begin
            if (violations > 0) $fatal(1, "TARGET FAIL: unknown predicate was crossed");
            $display("TARGET PASS: predicate-safe completion preserved");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 valid = 1; #40 ready = 1; #20 ready = 0; #20 valid = 0;
    end
endmodule
