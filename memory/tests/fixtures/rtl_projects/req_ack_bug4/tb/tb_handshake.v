module tb_handshake;
    reg clk = 0, rst_n = 0, start = 0, ready = 0;
    wire done;
    integer violations = 0;
    integer cycles = 0;
    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .start(start),
                     .ready(ready), .done(done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        if (dut.state == 2'd1 && dut.next_state == 2'd2 && !ready)
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
        #10 rst_n = 1; #10 start = 1; #40 ready = 1;
        #20 ready = 0; #20 start = 0;
    end
endmodule
