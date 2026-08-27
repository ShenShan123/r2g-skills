module ast_literal_c (
    input wire sel,
    output reg out
);
    always @(*) begin
        if (sel) out = 1'b0;
        else out = 1'b0;
    end
endmodule
