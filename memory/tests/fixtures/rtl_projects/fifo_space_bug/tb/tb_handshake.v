module tb_handshake;
    reg clk = 0, rst_n = 0, dequeue = 0, space_return = 0;
    wire done;
    integer violations = 0, cycles = 0;
    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .dequeue(dequeue), .space_return(space_return), .done(done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        if (dut.state == 2'd1 && dut.next_state == 2'd2 && !space_return)
            violations = violations + 1;
        cycles = cycles + 1;
        if (cycles > 100) begin
            if (violations > 0) $fatal(1, "TARGET FAIL: dequeue completed before capacity return");
            $display("TARGET PASS: FIFO capacity completion preserved");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 dequeue = 1; #40 space_return = 1; #20 space_return = 0; #20 dequeue = 0;
    end
endmodule
