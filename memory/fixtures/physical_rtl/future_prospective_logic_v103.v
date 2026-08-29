module future_prospective_logic_v103 (
    input wire clk, input wire rst_n, input wire advance,
    input wire [7:0] payload, output reg [9:0] state, output reg busy
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin state <= 10'h155; busy <= 1'b0; end
    else begin
      busy <= advance;
      if (advance) state <= (state << 1) ^ {2'b00, payload} ^ 10'h18f;
    end
  end
endmodule
