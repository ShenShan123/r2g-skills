module future_prospective_logic_v19 (
    input         clk,
    input         valid,
    input  [7:0]  a,
    input  [7:0]  b,
    input  [7:0]  c,
    output reg [15:0] result,
    output reg        ready
);
  always @(posedge clk) begin
    ready <= valid;
    if (valid)
      result <= ({8'b0, a} << 2) + (b * c);
    else
      result <= result - 16'h0007;
  end
endmodule
