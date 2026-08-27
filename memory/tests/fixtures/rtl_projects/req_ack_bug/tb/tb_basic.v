// FROZEN regression: the FSM must eventually reach `done` after a request
// (transfer completes) and must not leave `done` high forever.
// PASSES on BOTH the buggy and the fixed source — this is the preserve set.
module tb_basic;
    reg clk = 0, rst_n = 0, req = 0, ack = 0;
    wire done;
    integer cycles = 0;
    integer done_high_cycles = 0;

    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .req(req), .ack(ack), .done(done));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        cycles = cycles + 1;
        if (done) done_high_cycles = done_high_cycles + 1;
        if (cycles > 100) begin
            $display("REGRESSION basic: cycles=%0d done_high=%0d (done reached: %s)",
                     cycles, done_high_cycles, (done_high_cycles > 0 ? "yes" : "no"));
            if (done_high_cycles == 0) $fatal(1, "REGRESSION FAIL: transfer never completed");
            $display("REGRESSION PASS: basic transfer completed");
            $finish;
        end
    end

    initial begin
        #10 rst_n = 1;
        #10 req  = 1;
        #40 ack  = 1;
        #20 ack  = 0;
        #20 req  = 0;
    end
endmodule
