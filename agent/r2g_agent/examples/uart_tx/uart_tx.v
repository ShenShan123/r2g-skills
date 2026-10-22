// UART transmitter, 8N1, parameterised baud divider (r2g demo design).
module uart_tx #(
    parameter integer CLKS_PER_BIT = 16
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       start,
    input  wire [7:0] data,
    output reg        tx,
    output wire       busy
);
    localparam IDLE = 2'd0, START = 2'd1, DATA = 2'd2, STOP = 2'd3;
    reg [1:0]  state;
    reg [15:0] cnt;
    reg [2:0]  bit_idx;
    reg [7:0]  shreg;
    assign busy = (state != IDLE);
    wire tick = (cnt == CLKS_PER_BIT - 1);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE; cnt <= 0; bit_idx <= 0; shreg <= 0; tx <= 1'b1;
        end else begin
            cnt <= (state == IDLE || tick) ? 16'd0 : cnt + 16'd1;
            case (state)
                IDLE:  begin tx <= 1'b1; if (start) begin shreg <= data; state <= START; end end
                START: begin tx <= 1'b0; if (tick) begin state <= DATA; bit_idx <= 0; end end
                DATA:  begin tx <= shreg[bit_idx];
                             if (tick) begin
                                 if (bit_idx == 3'd7) state <= STOP;
                                 bit_idx <= bit_idx + 3'd1;
                             end
                       end
                STOP:  begin tx <= 1'b1; if (tick) state <= IDLE; end
            endcase
        end
    end
endmodule
