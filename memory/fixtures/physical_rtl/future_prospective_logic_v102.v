module future_prospective_logic_v102 (
    input wire clk, input wire rst_n, input wire enable,
    input wire [7:0] data, output reg [10:0] accumulator, output reg valid
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin accumulator <= 11'h000; valid <= 1'b0; end
    else begin
      valid <= enable;
      if (enable) accumulator <= accumulator + {3'b000, data} + 11'h09b;
    end
  end
endmodule
