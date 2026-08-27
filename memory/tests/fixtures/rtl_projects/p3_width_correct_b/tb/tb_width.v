module tb_width;
    reg [7:0] sample;
    wire [7:0] result;
    width_correct_b dut (.sample(sample), .result(result));
    initial begin
        sample = 8'b10100110;
        #1;
        if (result !== 8'b10100110) $fatal(1, "TARGET FAIL: width was truncated");
        $display("TARGET PASS: full width preserved");
        $finish;
    end
endmodule
