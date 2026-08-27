module tb_basic;
    reg clk = 0, rst_n = 0, req = 0, ack = 0;
    wire done;
    integer cycles = 0, done_high_cycles = 0;
    role_collision_fsm dut (.clk(clk), .rst_n(rst_n), .req(req), .ack(ack), .done(done));
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
        #10 rst_n = 1; #10 req = 1; #40 ack = 1; #20 ack = 0; #20 req = 0;
    end
endmodule
