module future_prospective_logic_v25 (
    input wire clk,
    input wire enable,
    input wire [31:0] data_in,
    output reg [31:0] accumulator,
    output reg active
);
  always @(posedge clk) begin
    active <= enable;
    if (enable)
      accumulator <= (accumulator + data_in) ^ 32'h5a5a00ff;
  end
endmodule
