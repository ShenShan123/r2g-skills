module future_prospective_logic_v18 (
    input         clk,
    input         enable,
    input  [15:0] left,
    input  [15:0] right,
    output reg [15:0] value,
    output reg        ready
);
  always @(posedge clk) begin
    ready <= enable;
    if (enable)
      value <= (left + right) ^ (left >> 2);
    else
      value <= value + 16'h0005;
  end
endmodule
