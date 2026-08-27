module tb_basic;
    reg clk = 0, rst_n = 0, valid = 0, ready = 0;
    wire commit;
    integer cycles = 0, commit_high_cycles = 0;
    req_ack_fsm dut (.clk(clk), .rst_n(rst_n), .valid(valid), .ready(ready), .commit(commit));
    always #5 clk = ~clk;
    always @(posedge clk) begin
        cycles = cycles + 1;
        if (commit) commit_high_cycles = commit_high_cycles + 1;
        if (cycles > 100) begin
            if (commit_high_cycles == 0) $fatal(1, "REGRESSION FAIL: commit never completed");
            $display("REGRESSION PASS: valid/ready commit completed");
            $finish;
        end
    end
    initial begin
        #10 rst_n = 1; #10 valid = 1; #40 ready = 1; #20 ready = 0; #20 valid = 0;
    end
endmodule
