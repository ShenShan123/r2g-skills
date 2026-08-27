// Future lineage: structural role collision.
module role_collision_fsm (
    input wire clk, input wire rst_n, input wire req, input wire ack,
    output reg done
);
    localparam [1:0] IDLE = 2'd0, CAPTURE = 2'd1, COMMIT = 2'd2;
    reg [1:0] state, next_state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= IDLE;
        else state <= next_state;
    end

    always @(*) begin
        next_state = state;
        case (state)
            IDLE: if (req) next_state = CAPTURE;
            CAPTURE: next_state = COMMIT; // BUG: role requires ack
            COMMIT: next_state = IDLE;
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) done <= 1'b0;
        else done <= (state == COMMIT);
    end
endmodule
