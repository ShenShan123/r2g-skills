// Future lineage: a degenerate always-true rule must be blocked by validity.
module validity_boundary_fsm (
    input wire clk, input wire rst_n, input wire start, input wire ack,
    output reg done
);
    localparam [1:0] IDLE = 2'd0, ARM = 2'd1, FINISH = 2'd2;
    reg [1:0] state, next_state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= IDLE;
        else state <= next_state;
    end

    always @(*) begin
        next_state = state;
        case (state)
            IDLE: if (start) next_state = ARM;
            ARM: next_state = FINISH; // BUG: ack is required
            FINISH: next_state = IDLE;
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) done <= 1'b0;
        else done <= (state == FINISH);
    end
endmodule
