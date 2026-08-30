module future_prospective_logic_v119 (
    input wire clk, input wire rst_n, input wire fire,
    input wire [7:0] value, output reg [9:0] sum, output reg valid
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin sum <= 10'h000; valid <= 1'b0; end
    else begin
      valid <= fire;
      if (fire) sum <= sum + {2'b00, value} + 10'h013;
    end
  end
endmodule
