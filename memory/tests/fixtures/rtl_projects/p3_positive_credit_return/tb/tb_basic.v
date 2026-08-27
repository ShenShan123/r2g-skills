module tb_basic;
    reg clk = 0, rst_n = 0, request = 0, credit_return = 0;
    wire done;
    integer cycles = 0, done_high_cycles = 0;
    positive_credit_return_fsm dut (.clk(clk), .rst_n(rst_n), .request(request),
                                    .credit_return(credit_return), .done(done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        cycles = cycles + 1;
        if (done) done_high_cycles = done_high_cycles + 1;
        if (cycles > 100) begin
            if (done_high_cycles == 0) $fatal(1, "REGRESSION FAIL: credit return never completed");
            $display("REGRESSION PASS: credit-return completion");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 request = 1; #40 credit_return = 1;
        #20 credit_return = 0; #20 request = 0;
    end
endmodule
