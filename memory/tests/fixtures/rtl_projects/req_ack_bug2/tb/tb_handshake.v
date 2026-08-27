// TARGET test: handshake property — WRITE -> VERIFY transition must only be
// taken when `wr_ack` is high. Same race-free property as req_ack_bug.
module tb_handshake;
    reg clk = 0, rst_n = 0, wr_req = 0, wr_ack = 0;
    wire wr_done;
    integer violations = 0;
    integer cycles = 0;

    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .wr_req(wr_req),
                     .wr_ack(wr_ack), .wr_done(wr_done));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (dut.state == 2'd1 && dut.next_state == 2'd2 && !wr_ack)
            violations = violations + 1;
        cycles = cycles + 1;
        if (cycles > 100) begin
            $display("TARGET handshake: violations=%0d (0 expected)", violations);
            if (violations > 0) $fatal(1, "TARGET FAIL: handshake completion violated");
            $display("TARGET PASS: handshake preserved");
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
