module tb_handshake;
    reg clk = 0, rst_n = 0, request = 0, credit_return = 0;
    wire done;
    integer violations = 0, cycles = 0;
    positive_credit_return_fsm dut (.clk(clk), .rst_n(rst_n), .request(request),
                                    .credit_return(credit_return), .done(done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        if (dut.state == 2'd1 && dut.next_state == 2'd2 && !credit_return)
            violations = violations + 1;
        cycles = cycles + 1;
        if (cycles > 100) begin
            if (violations > 0) $fatal(1, "TARGET FAIL: completion before credit return");
            $display("TARGET PASS: credit-return completion preserved");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 request = 1; #40 credit_return = 1;
        #20 credit_return = 0; #20 request = 0;
    end
endmodule
