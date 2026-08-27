module tb_basic;
    reg clk = 0, rst_n = 0, rd_req = 0, rd_ack = 0;
    wire rd_done;
    integer cycles = 0, done_high = 0;
    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .rd_req(rd_req), .rd_ack(rd_ack), .rd_done(rd_done));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        cycles = cycles + 1;
        if (rd_done) done_high = done_high + 1;
        if (cycles > 100) begin
            $display("REGRESSION basic: done_high=%0d", done_high);
            if (done_high == 0) $fatal(1, "REGRESSION FAIL");
            $display("REGRESSION PASS");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 rd_req = 1; #40 rd_ack = 1; #20 rd_ack = 0; #20 rd_req = 0;
    end
endmodule
