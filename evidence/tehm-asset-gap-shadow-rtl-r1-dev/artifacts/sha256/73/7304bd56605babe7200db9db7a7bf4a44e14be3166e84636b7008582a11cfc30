// req_ack_fsm — request/acknowledge handshake FSM.
//
// BUG (handshake_completion_violation): the SEND -> DONE transition fires
// without waiting for `ack`. The canonical fix is rtl.GUARD_STRENGTHEN:
//   SEND: next_state = DONE;  -->  SEND: if (ack) next_state = DONE;
//
// Fixed source (the "after" state):
//   SEND: if (ack) next_state = DONE;
module req_ack_fsm (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       req,
    input  wire       ack,
    output reg        done
);
    localparam [1:0] IDLE = 2'd0,
                     SEND = 2'd1,
                     DONE = 2'd2;

    reg [1:0] state;
    reg [1:0] next_state;

    // sequential state register
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            state <= IDLE;
        else
            state <= next_state;
    end

    // combinational next-state logic
    always @(*) begin
        next_state = state;
        case (state)
            IDLE: if (req) next_state = SEND;
            SEND: next_state = DONE;          // BUG: no ack guard
            DONE: next_state = IDLE;
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            done <= 1'b0;
        else
            done <= (state == DONE);
    end
endmodule
