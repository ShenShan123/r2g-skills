module future_prospective_logic_v36 (
    input wire clk, input wire enable, input wire [7:0] data_in,
    output reg [7:0] accumulator, output reg active
);
  always @(posedge clk) begin
    active <= enable;
    if (enable) accumulator <= (accumulator ^ data_in) + 8'h0b;
  end
endmodule
