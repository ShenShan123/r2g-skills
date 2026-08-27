module case_reorder_a (
    input wire req,
    input wire psel,
    output reg [1:0] state
);
    always @(*) begin
        casez ({req, psel})
            2'b1?: state = 2'd1;
            2'b?1: state = 2'd2;
            default: state = 2'd0;
        endcase
    end
endmodule
