module tb_basic;
    reg [5:0] payload;
    wire [5:0] payload_out;
    width_correct_c dut (.payload(payload), .payload_out(payload_out));
    initial begin
        payload = 6'b000011;
        #1;
        if (payload_out !== 6'b000011) $fatal(1, "REGRESSION FAIL: output mismatch");
        $display("REGRESSION PASS: output preserved");
        $finish;
    end
endmodule
