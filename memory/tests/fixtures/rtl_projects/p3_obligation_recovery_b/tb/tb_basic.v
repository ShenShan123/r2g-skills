module tb_basic;
    reg clk = 0, rst_n = 0, launch = 0, ack_reply = 0;
    wire complete;
    integer cycles = 0, complete_high_cycles = 0;
    obligation_recovery_b_fsm dut (.clk(clk), .rst_n(rst_n), .launch(launch),
                                   .ack_reply(ack_reply), .complete(complete));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        cycles = cycles + 1;
        if (complete) complete_high_cycles = complete_high_cycles + 1;
        if (cycles > 80) begin
            if (complete_high_cycles == 0) $fatal(1, "REGRESSION FAIL: completion disappeared");
            $display("REGRESSION PASS: completion reached");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 launch = 1; #40 ack_reply = 1;
        #20 ack_reply = 0; #20 launch = 0;
    end
endmodule
