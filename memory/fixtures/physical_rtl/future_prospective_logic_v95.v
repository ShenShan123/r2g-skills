module future_prospective_logic_v95 (
    input wire clk, input wire rst_n, input wire load,
    input wire [7:0] data_a, input wire [7:0] data_b, input wire [7:0] mask,
    output reg [7:0] selected, output reg valid
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin selected <= 8'h00; valid <= 1'b0; end
    else begin
      valid <= load;
      if (load) selected <= (data_a & mask) | (data_b & ~mask);
    end
  end
endmodule
