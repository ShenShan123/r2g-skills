// Independent sky130hs physical-training lineage for TEHM density evidence.
//
// This is intentionally not derived from the frozen SPI/GCD fixtures: it is a
// small multi-lane state machine with a distinct RTL topology and a stable
// clock/reset contract suitable for the ORFS platform template.
module hs_counter_bank (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       load,
    input  wire [7:0] seed,
    output wire [7:0] data_out,
    output reg        pulse
);
    reg [7:0] lane0, lane1, lane2, lane3;
    reg [7:0] lane4, lane5, lane6, lane7;

    always @(posedge clk) begin
        if (rst) begin
            lane0 <= 8'h00;
            lane1 <= 8'h11;
            lane2 <= 8'h22;
            lane3 <= 8'h33;
            lane4 <= 8'h44;
            lane5 <= 8'h55;
            lane6 <= 8'h66;
            lane7 <= 8'h77;
            pulse <= 1'b0;
        end else if (load) begin
            lane0 <= seed;
            lane1 <= seed ^ 8'h11;
            lane2 <= seed ^ 8'h22;
            lane3 <= seed ^ 8'h33;
            lane4 <= seed ^ 8'h44;
            lane5 <= seed ^ 8'h55;
            lane6 <= seed ^ 8'h66;
            lane7 <= seed ^ 8'h77;
            pulse <= 1'b0;
        end else if (en) begin
            lane0 <= lane0 + 8'd1;
            lane1 <= lane1 + 8'd3;
            lane2 <= lane2 + 8'd5;
            lane3 <= lane3 + 8'd7;
            lane4 <= lane4 + 8'd9;
            lane5 <= lane5 + 8'd11;
            lane6 <= lane6 + 8'd13;
            lane7 <= lane7 + 8'd15;
            pulse <= &lane0;
        end else begin
            pulse <= 1'b0;
        end
    end

    assign data_out = lane0 ^ lane1 ^ lane2 ^ lane3 ^
                      lane4 ^ lane5 ^ lane6 ^ lane7;
endmodule
