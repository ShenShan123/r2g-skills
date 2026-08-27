module future_prospective_logic_v15 (
    input         clk,
    input         valid,
    input  [7:0]  a,
    input  [7:0]  b,
    input  [7:0]  c,
    output reg [15:0] product,
    output reg        ready
);
  always @(posedge clk) begin
    ready <= valid;
    if (valid)
      product <= (a * b) + {8'b0, c};
    else
      product <= product ^ 16'h003c;
  end
endmodule
