module future_prospective_logic_v99 (
    input wire clk, input wire rst_n, input wire update,
    input wire [7:0] payload, output reg [8:0] state, output reg valid
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin state <= 9'h000; valid <= 1'b0; end
    else begin
      valid <= update;
      if (update) state <= ({1'b0, payload} - 9'h013) ^ 9'h12d;
    end
  end
endmodule
