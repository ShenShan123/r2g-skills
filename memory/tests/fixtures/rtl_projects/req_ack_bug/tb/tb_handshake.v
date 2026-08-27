// TARGET test: the handshake property — the SEND -> DONE transition must only
// be taken when `ack` is high (the acknowledge handshake completes first).
//
// Race-free property sampled at each clock edge:
//     in state SEND, next_state == DONE while ack is low  ==>  violation
//
// FAILS on the buggy source (SEND: next_state = DONE; has no ack guard);
// PASSES after rtl.GUARD_STRENGTHEN (SEND: if (ack) next_state = DONE;).
module tb_handshake;
    reg clk = 0, rst_n = 0, req = 0, ack = 0;
    wire done;
    integer violations = 0;
    integer cycles = 0;

    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .req(req), .ack(ack), .done(done));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (dut.state == 2'd1 && dut.next_state == 2'd2 && !ack)
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
        #10 req  = 1;       // request a transfer
        #40 ack  = 1;       // peer acknowledges
        #20 ack  = 0;
        #20 req  = 0;
    end
endmodule
