module tb_basic;
    reg clk = 0, rst_n = 0, launch = 0, reply = 0;
    wire done;
    integer cycles = 0, done_high_cycles = 0;
    obligation_recovery_fsm dut (.clk(clk), .rst_n(rst_n), .launch(launch), .reply(reply), .done(done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        cycles = cycles + 1;
        if (done) done_high_cycles = done_high_cycles + 1;
        if (cycles > 80) begin
            if (done_high_cycles == 0) $fatal(1, "REGRESSION FAIL: completion disappeared");
            $display("REGRESSION PASS: completion reached");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 launch = 1; #40 reply = 1; #20 reply = 0; #20 launch = 0;
    end
endmodule
