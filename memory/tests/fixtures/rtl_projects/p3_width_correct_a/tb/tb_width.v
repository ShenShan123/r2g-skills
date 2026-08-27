module tb_width;
    reg [3:0] data;
    wire [3:0] out;
    width_correct_a dut (.data(data), .out(out));
    initial begin
        data = 4'b1010;
        #1;
        if (out !== 4'b1010) $fatal(1, "TARGET FAIL: width was truncated");
        $display("TARGET PASS: full width preserved");
        $finish;
    end
endmodule
