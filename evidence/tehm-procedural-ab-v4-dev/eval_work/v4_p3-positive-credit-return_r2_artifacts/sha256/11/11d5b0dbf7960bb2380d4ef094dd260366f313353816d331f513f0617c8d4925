module overlap_priority_a (
    input wire clk,
    input wire rst_n,
    input wire req,
    input wire psel,
    output reg [1:0] state
);
    localparam [1:0] IDLE = 2'd0, BUSY = 2'd1, RETRY = 2'd2;
    reg [1:0] next_state;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= IDLE;
        else state <= next_state;
    end
    always @(*) begin
        next_state = state;
        casez ({req, psel})
            2'b1?: next_state = BUSY;  // BUG: broad match wins on overlap
            2'b?1: next_state = RETRY;
            default: next_state = IDLE;
        endcase
    end
endmodule
