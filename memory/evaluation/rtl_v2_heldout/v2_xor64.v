module v2_xor64(
    input wire clk,
    input wire rst,
    input wire en,
    input wire [63:0] seed,
    output reg [63:0] state
);
  wire feedback = state[63] ^ state[45] ^ state[17] ^ state[0];
  always @(posedge clk) begin
    if (rst) state <= 64'h1;
    else if (en) state <= {state[62:0], feedback} ^ seed;
  end
endmodule
