module future_prospective_logic_v97 (
    input wire clk, input wire rst_n, input wire enable,
    input wire [7:0] payload, output reg [9:0] encoded, output reg valid
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin encoded <= 10'h000; valid <= 1'b0; end
    else begin
      valid <= enable;
      if (enable) encoded <= {2'b00, payload} + ({2'b00, payload} << 1)
                                             + 10'h017;
    end
  end
endmodule
