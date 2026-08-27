module future_prospective_logic_v13 (
    input         clk,
    input         select,
    input  [15:0] left,
    input  [15:0] right,
    output reg [15:0] result
);
  always @(posedge clk) begin
    if (select)
      result <= (left + right) ^ 16'h00a5;
    else
      result <= (left - right) + {right[7:0], left[15:8]};
  end
endmodule
