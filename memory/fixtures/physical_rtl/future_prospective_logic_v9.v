module future_prospective_logic_v9 (
    input        clk,
    input        sel,
    input  [7:0] a,
    input  [7:0] b,
    output reg [7:0] y
);
  always @(posedge clk) begin
    if (sel)
      y <= a - b;
    else
      y <= {a[6:0], a[7]} + b;
  end
endmodule
