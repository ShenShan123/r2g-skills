module ast_literal_b (
    input wire sel,
    output reg out
);
    always @(*) begin
        if (sel) out = 1'b1;
        else out = 1'b1;
    end
endmodule
