// 8-bit ALU with registered result and flags (r2g demo design).
module alu8 (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] a,
    input  wire [7:0] b,
    input  wire [2:0] op,
    output reg  [7:0] y,
    output reg        carry,
    output reg        zero
);
    reg [8:0] r;
    always @(*) begin
        case (op)
            3'd0: r = {1'b0, a} + {1'b0, b};
            3'd1: r = {1'b0, a} - {1'b0, b};
            3'd2: r = {1'b0, a & b};
            3'd3: r = {1'b0, a | b};
            3'd4: r = {1'b0, a ^ b};
            3'd5: r = {a, 1'b0};
            3'd6: r = {1'b0, a[0], a[7:1]};
            default: r = {1'b0, a * b};
        endcase
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            y <= 8'd0; carry <= 1'b0; zero <= 1'b1;
        end else begin
            y <= r[7:0]; carry <= r[8]; zero <= (r[7:0] == 8'd0);
        end
    end
endmodule
