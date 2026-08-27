module future_prospective_logic_v73 (
    input wire clk, input wire active, input wire [7:0] payload,
    output reg [7:0] value, output reg busy
);
  always @(posedge clk) begin
    busy <= active;
    if (active) value <= payload - 8'h0e;
  end
endmodule
