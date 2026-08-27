module tb_handshake;
    reg clk = 0, rst_n = 0, req = 0, ack = 0;
    wire done;
    integer violations = 0, cycles = 0;
    role_collision_fsm dut (.clk(clk), .rst_n(rst_n), .req(req), .ack(ack), .done(done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        if (dut.state == 2'd1 && dut.next_state == 2'd2 && !ack)
            violations = violations + 1;
        cycles = cycles + 1;
        if (cycles > 80) begin
            if (violations > 0) $fatal(1, "TARGET FAIL: structural role completion violated");
            $display("TARGET PASS: role-aware completion preserved");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 req = 1; #40 ack = 1; #20 ack = 0; #20 req = 0;
    end
endmodule
