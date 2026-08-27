module future_calibration_logic_v11 (
    input         clk,
    input         valid,
    input  [15:0] data,
    input  [3:0]  shift,
    output reg [15:0] acc,
    output reg        ready
);
  always @(posedge clk) begin
    ready <= valid;
    if (valid)
      acc <= (data << shift) + data;
    else
      acc <= acc ^ 16'h003d;
  end
endmodule
