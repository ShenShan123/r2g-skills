// FROZEN regression: the FSM must eventually reach wr_done after a request.
// PASSES on both the buggy and the fixed source (the preserve set).
module tb_basic;
    reg clk = 0, rst_n = 0, wr_req = 0, wr_ack = 0;
    wire wr_done;
    integer cycles = 0;
    integer done_high_cycles = 0;

    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .wr_req(wr_req),
                     .wr_ack(wr_ack), .wr_done(wr_done));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        cycles = cycles + 1;
        if (wr_done) done_high_cycles = done_high_cycles + 1;
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
        #10 wr_req = 1;
        #40 wr_ack = 1;
        #20 wr_ack = 0;
        #20 wr_req = 0;
    end
endmodule
