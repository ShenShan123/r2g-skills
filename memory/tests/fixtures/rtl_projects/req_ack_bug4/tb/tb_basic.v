module tb_basic;
    reg clk = 0, rst_n = 0, start = 0, ready = 0;
    wire done;
    integer cycles = 0, done_high = 0;
    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .start(start),
                     .ready(ready), .done(done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        cycles = cycles + 1;
        if (done) done_high = done_high + 1;
        if (cycles > 100) begin
            $display("REGRESSION basic: done_high=%0d", done_high);
            if (done_high == 0) $fatal(1, "REGRESSION FAIL");
            $display("REGRESSION PASS");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 start = 1; #40 ready = 1;
        #20 ready = 0; #20 start = 0;
    end
endmodule
