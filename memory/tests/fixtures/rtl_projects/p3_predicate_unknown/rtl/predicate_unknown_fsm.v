// Future lineage: an unobservable predicate must not be treated as false/true.
module predicate_unknown_fsm (
    input wire clk, input wire rst_n, input wire valid, input wire ready,
    output reg accepted
);
    localparam [1:0] IDLE = 2'd0, WAIT = 2'd1, COMMIT = 2'd2;
    reg [1:0] state, next_state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= IDLE;
        else state <= next_state;
    end

    always @(*) begin
        next_state = state;
        case (state)
            IDLE: if (valid) next_state = WAIT;
            WAIT: next_state = COMMIT; // BUG: ready predicate is required
            COMMIT: next_state = IDLE;
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) accepted <= 1'b0;
        else accepted <= (state == COMMIT);
    end
endmodule
