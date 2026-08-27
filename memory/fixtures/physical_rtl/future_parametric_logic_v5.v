module future_parametric_logic_v5 (
    input        clk,
    input        mode,
    input  [7:0] a,
    input  [7:0] b,
    output reg [7:0] y
);
  always @(posedge clk) begin
    if (mode)
      y <= a - b;
    else
      y <= {a[6:0], a[7]} + b;
  end
endmodule
