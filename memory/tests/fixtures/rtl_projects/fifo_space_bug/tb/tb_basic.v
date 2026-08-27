module tb_basic;
    reg clk = 0, rst_n = 0, dequeue = 0, space_return = 0;
    wire done;
    integer cycles = 0, done_high_cycles = 0;
    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .dequeue(dequeue), .space_return(space_return), .done(done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        cycles = cycles + 1;
        if (done) done_high_cycles = done_high_cycles + 1;
        if (cycles > 100) begin
            if (done_high_cycles == 0) $fatal(1, "REGRESSION FAIL: dequeue never completed");
            $display("REGRESSION PASS: FIFO dequeue completed");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 dequeue = 1; #40 space_return = 1; #20 space_return = 0; #20 dequeue = 0;
    end
endmodule
