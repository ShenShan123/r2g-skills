module future_prospective_logic_v6 (
    input        clk,
    input        sel,
    input  [7:0] a,
    input  [7:0] b,
    output reg [7:0] y
);
  always @(posedge clk) begin
    if (sel)
      y <= (a + b) ^ 8'h3c;
    else
      y <= (a & b) | {a[0], a[7:1]};
  end
endmodule
