module tb_handshake;
    reg clk = 0, rst_n = 0, rd_req = 0, rd_ack = 0;
    wire rd_done;
    integer violations = 0;
    integer cycles = 0;
    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .rd_req(rd_req), .rd_ack(rd_ack), .rd_done(rd_done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        if (dut.state == 2'd1 && dut.next_state == 2'd2 && !rd_ack) violations = violations + 1;
        cycles = cycles + 1;
        if (cycles > 100) begin
            $display("TARGET handshake: violations=%0d (0 expected)", violations);
            if (violations > 0) $fatal(1, "TARGET FAIL: handshake completion violated");
            $display("TARGET PASS: handshake preserved");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 rd_req = 1; #40 rd_ack = 1; #20 rd_ack = 0; #20 rd_req = 0;
    end
endmodule
