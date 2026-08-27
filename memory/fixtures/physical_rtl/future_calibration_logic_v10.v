module future_calibration_logic_v10 (
    input         clk,
    input         en,
    input  [7:0]  a,
    input  [7:0]  b,
    input  [7:0]  c,
    output reg [15:0] y
);
  always @(posedge clk) begin
    if (en)
      y <= (a * b) + {8'b0, c};
    else
      y <= {a, b} ^ {c, a};
  end
endmodule
