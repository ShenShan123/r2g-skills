// req_ack_fsm — independent READY handshake lineage for held-out v2.
// BUG: WAIT advances to DONE without waiting for ready.
module req_ack_fsm (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       start,
    input  wire       ready,
    output reg        done
);
    localparam [1:0] IDLE = 2'd0,
                     WAIT = 2'd1,
                     DONE = 2'd2;

    reg [1:0] state;
    reg [1:0] next_state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= IDLE;
        else state <= next_state;
    end

    always @(*) begin
        next_state = state;
        case (state)
            IDLE: if (start) next_state = WAIT;
            WAIT: next_state = DONE;       // BUG: no ready guard
            DONE: next_state = IDLE;
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) done <= 1'b0;
        else done <= (state == DONE);
    end
endmodule
