module width_correct_b (
    input wire [7:0] sample,
    output reg [7:0] result
);
    always @(*) begin
        result = sample[3:0]; // BUG: truncates the upper sample bits
    end
endmodule
