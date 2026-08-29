module future_prospective_logic_v91 (
    input wire clk, input wire active, input wire [7:0] payload,
    output reg [9:0] value, output reg busy
);
  always @(posedge clk) begin
    busy <= active;
    if (active) value <= {2'b00, payload} + ({2'b00, payload} << 1)
                                           - 10'h01d;
  end
endmodule
