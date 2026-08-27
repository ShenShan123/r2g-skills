module selector_xor32(
    input wire clk,
    input wire rst,
    input wire en,
    input wire [31:0] seed,
    output reg [31:0] state
);
  wire feedback = state[31] ^ state[21] ^ state[1] ^ state[0];
  always @(posedge clk) begin
    if (rst) state <= 32'h1;
    else if (en) state <= {state[30:0], feedback} ^ seed;
  end
endmodule
