module future_prospective_logic_v14 (
    input         clk,
    input         enable,
    input  [15:0] x,
    input  [15:0] y,
    output reg [15:0] result,
    output reg        valid
);
  always @(posedge clk) begin
    valid <= enable;
    if (enable)
      result <= (x ^ y) + (x << 1);
    else
      result <= result + 16'h0003;
  end
endmodule
