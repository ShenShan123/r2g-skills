module tb_width;
    reg [5:0] payload;
    wire [5:0] payload_out;
    width_correct_c dut (.payload(payload), .payload_out(payload_out));
    initial begin
        payload = 6'b110101;
        #1;
        if (payload_out !== 6'b110101) $fatal(1, "TARGET FAIL: width was truncated");
        $display("TARGET PASS: full width preserved");
        $finish;
    end
endmodule
