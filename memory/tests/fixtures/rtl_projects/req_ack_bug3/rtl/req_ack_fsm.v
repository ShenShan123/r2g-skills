// req_ack_fsm — READ handshake variant (held-out: NOT in the training set).
// Same mechanism (handshake completion violation), different names:
//   BUG:   RCV: next_state = RD_DONE;          (no rd_ack guard)
//   FIX:   RCV: if (rd_ack) next_state = RD_DONE;
module req_ack_fsm (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       rd_req,
    input  wire       rd_ack,
    output reg        rd_done
);
    localparam [1:0] IDLE = 2'd0,
                     RCV  = 2'd1,
                     RD_DONE = 2'd2;

    reg [1:0] state;
    reg [1:0] next_state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= IDLE;
        else state <= next_state;
    end

    always @(*) begin
        next_state = state;
        case (state)
            IDLE:   if (rd_req) next_state = RCV;
            RCV:    next_state = RD_DONE;       // BUG: no rd_ack guard
            RD_DONE: next_state = IDLE;
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) rd_done <= 1'b0;
        else rd_done <= (state == RD_DONE);
    end
endmodule
