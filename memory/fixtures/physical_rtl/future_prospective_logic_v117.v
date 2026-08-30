module future_prospective_logic_v117 (
    input wire clk, input wire rst_n, input wire enable,
    input wire [7:0] payload, output reg [10:0] encoded, output reg valid
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin encoded <= 11'h000; valid <= 1'b0; end
    else begin
      valid <= enable;
      if (enable) encoded <= {3'b000, payload} + ({3'b000, payload} << 1)
                                             + 11'h05d;
    end
  end
endmodule
