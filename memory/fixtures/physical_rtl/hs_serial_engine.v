// Independent serial-engine lineage for physical density calibration.
// It intentionally has a SPI-like pin count but a different RTL structure:
// four state phases, a rotating payload accumulator, and parity feedback.
module hs_serial_engine (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       start,
    input  wire       miso,
    input  wire [7:0] payload,
    output reg        cs_n,
    output reg        sclk,
    output reg        mosi,
    output reg [7:0]  result
);
    localparam [1:0] IDLE = 2'd0, LOAD = 2'd1, SHIFT = 2'd2, FLUSH = 2'd3;
    reg [1:0] state;
    reg [3:0] count;
    reg [7:0] shift_reg;
    reg [7:0] accumulator;
    reg       parity;
    wire [7:0] rotated = {shift_reg[6:0], shift_reg[7]};
    wire [7:0] mixed = rotated ^ payload ^ {7{parity}} ^ {7'b0, miso};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state       <= IDLE;
            count       <= 4'd0;
            shift_reg   <= 8'd0;
            accumulator <= 8'd0;
            parity      <= 1'b0;
            result      <= 8'd0;
            cs_n        <= 1'b1;
            sclk        <= 1'b0;
            mosi        <= 1'b0;
        end else begin
            case (state)
                IDLE: begin
                    cs_n <= 1'b1;
                    sclk <= 1'b0;
                    if (start) begin
                        state     <= LOAD;
                        count     <= 4'd0;
                        shift_reg <= payload;
                    end
                end
                LOAD: begin
                    cs_n   <= 1'b0;
                    sclk   <= ~sclk;
                    mosi   <= shift_reg[7];
                    state  <= SHIFT;
                end
                SHIFT: begin
                    cs_n        <= 1'b0;
                    sclk        <= ~sclk;
                    mosi        <= shift_reg[7];
                    shift_reg   <= rotated;
                    accumulator <= accumulator + mixed;
                    parity      <= ^mixed;
                    count       <= count + 1'b1;
                    if (count == 4'd7)
                        state <= FLUSH;
                end
                FLUSH: begin
                    cs_n   <= 1'b1;
                    sclk   <= 1'b0;
                    result <= accumulator ^ mixed;
                    state  <= IDLE;
                end
            endcase
        end
    end
endmodule
