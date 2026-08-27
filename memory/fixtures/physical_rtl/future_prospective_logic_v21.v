module future_prospective_logic_v21 (
    input         clk,
    input         enable,
    input  [7:0]  data_in,
    input  [7:0]  mask,
    output reg [7:0]  accumulator,
    output reg        active
);
  always @(posedge clk) begin
    active <= enable;
    if (enable)
      accumulator <= (accumulator + data_in) ^ mask;
    else
      accumulator <= accumulator;
  end
endmodule
