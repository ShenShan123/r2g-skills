module future_prospective_logic_v87 (
    input wire clk, input wire rst_n, input wire enable,
    input wire [7:0] data_a, input wire [7:0] data_b,
    output reg [8:0] accumulator, output reg valid
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin accumulator <= 9'h000; valid <= 1'b0; end
    else begin
      valid <= enable;
      if (enable) accumulator <= accumulator + {1'b0, data_a}
                                           + {1'b0, data_b} + 9'h073;
    end
  end
endmodule
