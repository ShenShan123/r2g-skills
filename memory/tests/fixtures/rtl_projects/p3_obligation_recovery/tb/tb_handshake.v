module tb_handshake;
    reg clk = 0, rst_n = 0, launch = 0, reply = 0;
    wire done;
    integer violations = 0, cycles = 0;
    obligation_recovery_fsm dut (.clk(clk), .rst_n(rst_n), .launch(launch), .reply(reply), .done(done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        if (dut.state == 2'd1 && dut.next_state == 2'd2 && !reply)
            violations = violations + 1;
        cycles = cycles + 1;
        if (cycles > 80) begin
            if (violations > 0) $fatal(1, "TARGET FAIL: recovery obligation violated");
            $display("TARGET PASS: recovery obligation preserved");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 launch = 1; #40 reply = 1; #20 reply = 0; #20 launch = 0;
    end
endmodule
