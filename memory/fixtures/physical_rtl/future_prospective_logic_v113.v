module future_prospective_logic_v113 (
    input wire clk, input wire rst_n, input wire enable,
    input wire [7:0] data, output reg [9:0] accumulator, output reg valid
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin accumulator <= 10'h000; valid <= 1'b0; end
    else begin
      valid <= enable;
      if (enable) accumulator <= accumulator + {2'b00, data} + 10'h01b;
    end
  end
endmodule
