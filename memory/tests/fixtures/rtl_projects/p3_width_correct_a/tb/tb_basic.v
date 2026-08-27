module tb_basic;
    reg [3:0] data;
    wire [3:0] out;
    width_correct_a dut (.data(data), .out(out));
    initial begin
        data = 4'b0011;
        #1;
        if (out !== 4'b0011) $fatal(1, "REGRESSION FAIL: output mismatch");
        $display("REGRESSION PASS: output preserved");
        $finish;
    end
endmodule
