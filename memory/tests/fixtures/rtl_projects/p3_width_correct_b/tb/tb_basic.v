module tb_basic;
    reg [7:0] sample;
    wire [7:0] result;
    width_correct_b dut (.sample(sample), .result(result));
    initial begin
        sample = 8'b00001111;
        #1;
        if (result !== 8'b00001111) $fatal(1, "REGRESSION FAIL: output mismatch");
        $display("REGRESSION PASS: output preserved");
        $finish;
    end
endmodule
