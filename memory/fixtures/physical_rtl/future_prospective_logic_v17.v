module future_prospective_logic_v17 (
    input         clk,
    input         valid,
    input  [7:0]  a,
    input  [7:0]  b,
    output reg [15:0] sum,
    output reg        ready
);
  always @(posedge clk) begin
    ready <= valid;
    if (valid)
      sum <= {8'b0, a} + {8'b0, b} + 16'h0011;
    else
      sum <= sum - 16'h0001;
  end
endmodule
