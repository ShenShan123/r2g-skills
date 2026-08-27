module v2_cal_hash16(
    input wire clk,
    input wire rst,
    input wire valid,
    input wire [7:0] byte_in,
    output reg [15:0] digest
);
  wire feedback = digest[15] ^ digest[12] ^ digest[5];
  always @(posedge clk) begin
    if (rst) digest <= 16'h1d0f;
    else if (valid) begin
      digest <= {digest[14:0], feedback} ^ {byte_in, byte_in};
    end
  end
endmodule
