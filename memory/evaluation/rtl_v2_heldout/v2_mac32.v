module v2_mac32(
    input wire clk,
    input wire rst,
    input wire en,
    input wire [15:0] a,
    input wire [15:0] b,
    input wire clear,
    output reg [31:0] acc
);
  wire [31:0] product = a * b;
  always @(posedge clk) begin
    if (rst || clear) acc <= 32'b0;
    else if (en) acc <= acc + product;
  end
endmodule
