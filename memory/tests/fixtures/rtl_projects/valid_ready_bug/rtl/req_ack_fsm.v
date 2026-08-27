// Independent valid/ready commit protocol fixture.
module req_ack_fsm (
    input wire clk, input wire rst_n, input wire valid, input wire ready,
    output reg commit
);
    localparam [1:0] IDLE = 2'd0, ACCEPT = 2'd1, COMMIT = 2'd2;
    reg [1:0] state, next_state;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= IDLE;
        else state <= next_state;
    end
    always @(*) begin
        next_state = state;
        case (state)
            IDLE: if (valid) next_state = ACCEPT;
            ACCEPT: next_state = COMMIT; // BUG: commit must wait for ready
            COMMIT: next_state = IDLE;
        endcase
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) commit <= 1'b0;
        else commit <= (state == COMMIT);
    end
endmodule
