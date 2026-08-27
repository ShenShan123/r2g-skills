module future_prospective_logic_v12 (
    input         clk,
    input         valid,
    input  [7:0]  a,
    input  [7:0]  b,
    input  [3:0]  shift,
    output reg [15:0] y,
    output reg        ready
);
  always @(posedge clk) begin
    ready <= valid;
    if (valid)
      y <= ({8'b0, a} << shift) + {8'b0, b};
    else
      y <= y ^ 16'h005a;
  end
endmodule
